"""Gate orchestration (Phase 5 / T26-T44) - the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a budgeted
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Budgeted rework / reject (2); waive
advances without signing and marks the delivery not-"ready" (INV-1).
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import agent_loader, wave_engine, sign
from ..projects import validate_project_id
from .agent_loop import AgentLoop, LoopResult
from .wiring_check import CODE_SUFFIXES, SCAFFOLD_NAMES, find_unwired_modules
from .budgets import resolve_gate_reopen_budget, resolve_gate_rework_budget
from .enforcement_state import EnforcementProvider
from .release_tree import ReleaseTreeError
from .release_tree import tree_digest as release_tree_digest
from .release_tree import workspace_path, workspace_release_tree
from .run_ids import agent_run_dir, safe_control_path, validate_run_id

__all__ = ["GateOrchestrator", "GATE_SPECIALISTS", "GATE_QUESTIONS", "GATE_ROLES",
           "resume_delivery"]

GATE_ORDER = ["G0", "G1", "G2", "G3", "G4", "G5"]

GATE_SPECIALISTS = {
    "G0": "Product Strategist",
    "G1": "Domain Analyst",
    "G2": "Solution Architect",
    "G3": "UX Designer",
    "G4": "Full-Stack Engineer",
    "G5": "Release Manager",
}

GATE_QUESTIONS = {
    "G0": "Is this what you want built?",
    "G1": "Does this capture the domain correctly?",
    "G2": "Approve the build plan?",
    "G3": "Does this design look right?",
    "G4": "Is the build acceptable?",
    "G5": "Ready to ship?",
}

# Role authorised to sign each gate. Must be valid for EVERY artifact in the
# gate (sign_gate rejects a role not authorised for any present artifact).
# G3 mixes PO+PE artifacts -> co-signed per-artifact in _default_sign.
GATE_ROLES = {
    "G0": "PE", "G1": "PO", "G2": "PO", "G3": "PE", "G4": "PE", "G5": "QA",
}

_SIGN_VERDICT = {
    "approve": "APPROVED",
    "approve-with-conditions": "APPROVED-WITH-CONDITIONS",
}
_KNOWN_VERDICTS = {"approve", "approve-with-conditions", "request-changes", "reject", "waive"}

# Fix 1: AgentLoop LoopResult.status values that are NOT a successful, reviewable
# outcome. A gate whose agent finished in any of these states (or wrote no files)
# must NOT open for a verdict -- the walk previously DISCARDED the LoopResult and
# opened review unconditionally, so a refused/errored/stalled/no-file gate was
# still approvable. "completed" is the only success status the loop stamps.
_UNREVIEWABLE_LOOP_STATUSES = frozenset({
    "error", "cancelled", "budget_exhausted", "stalled_no_tool",
    "max_tokens", "text_only",
})

# ---------------------------------------------------------------------------
# Engine PROFILES (cross-vendor panel decision: ONE engine, config-gated
# profiles -- NOT two engines). A profile selects which POST-BUILD release-
# safety stages run AFTER the G4 build is independently verified. Every gated
# stage runs STRICTLY where it cannot change the G4 build outcome, the scores,
# or the product bytes of the benchmark profile.
#
#   * "benchmark" (the SAFE DEFAULT): runs NONE of the extra stages, so the
#     benchmark path is BEHAVIOR-IDENTICAL to today -- deterministic, no new
#     blocking/flaky stages (benchmark variance is poison). The grader already
#     scores security, and the behavioral acceptance tests already own "the app
#     runs", so re-running them here would only add noise and flake.
#   * "production": adds the release-safety stages -- a real security gate
#     (gitleaks/semgrep-style scan; a CRITICAL finding HARD-BLOCKS the sign) and
#     real runtime/UX proof (a live dev server and executed browser page). These
#     stages do not rewrite the G4 build result, but missing/failing evidence
#     fails the later G5 release decision closed.
#
# Each stage is a single existing implementation (security_gate.run_security_gate,
# proof.run_runtime_proof / run_ux_proof) -- called here, never reimplemented.
DEFAULT_PROFILE = "benchmark"

PROFILE_STAGES: dict[str, dict[str, bool]] = {
    # security_gate OFF, runtime_proof OFF -> byte-identical to today.
    "benchmark": {"security_gate": False, "runtime_proof": False},
    "production": {"security_gate": True, "runtime_proof": True},
}


def validate_orchestrator_profile(profile: str) -> str:
    """Return a canonical engine profile or fail closed.

    Profile selection changes release enforcement, so silently converting a
    typo to the less strict benchmark profile is unsafe.  Defaults are chosen
    by callers; once a value reaches the engine it must be one of the declared
    profiles.
    """
    value = str(profile or "").strip()
    if value not in PROFILE_STAGES:
        allowed = ", ".join(sorted(PROFILE_STAGES))
        raise ValueError(
            f"unknown orchestrator profile {value!r}; expected one of: {allowed}"
        )
    return value


def _is_real_product_src(rel_path: str) -> bool:
    """A real product source/test file the G4 build must have produced — not
    governance bookkeeping (.signalos/core/) or lockfiles."""
    p = str(rel_path).replace("\\", "/").lstrip("/")
    if not p or p.startswith((".signalos/", ".git/", "node_modules/", "core/")):
        return False
    return p.startswith((
        "src/", "app/", "components/", "lib/", "pages/",
        "tests/", "test/", "server/", "api/",
    ))


@dataclass
class DeliveryState:
    run_id: str
    prompt: str
    current_gate: str = "G0"
    status: str = "active"
    # §3.2: the project namespace this delivery signs/generates artifacts in.
    # Persisted so resume_delivery restores the SAME namespace binding (an
    # older persisted state without the field resumes as "default").
    project_id: str = "default"
    # Release enforcement is part of the durable delivery identity.  A restart
    # must never downgrade a production run to benchmark semantics.
    profile: str = DEFAULT_PROFILE
    # Preserve the signer selected when the delivery began.  Identity changes
    # outside the run must not rewrite who is recorded on later resumed gates.
    signer: str = "foundry-agent"
    rework: dict = field(default_factory=dict)
    rejections: dict = field(default_factory=dict)
    signed: list = field(default_factory=list)
    waived: list = field(default_factory=list)
    # Reviewer feedback per gate, in verdict order. Each entry is
    # {"verdict": "request-changes"|"reject"|"reopen", "cycle": int,
    # "feedback": str}. Persisted with the rest of the state so a resumed
    # delivery still knows what the reviewer actually asked for. Older
    # persisted states lack this field; resume_delivery tolerates its
    # absence (defaults to {}).
    feedback: dict = field(default_factory=dict)
    # Gate-reopen bookkeeping (#4). `reopens` counts reopen cycles per gate
    # (budgeted like rework/rejections). `invalidated` is the append-only
    # record of every signature/waiver removed by a reopen cascade:
    # {"gate", "by", "reason", "cascade": bool[, "waived": True]}.
    # Both are absent from older persisted states; resume_delivery defaults.
    reopens: dict = field(default_factory=dict)
    invalidated: list = field(default_factory=list)
    # Fix 5: approve-with-conditions bookkeeping. Each recorded condition
    # (gate -> condition text) is UNRESOLVED (there is no resolve API yet) and
    # blocks delivery readiness at G5, so an "approve with conditions" can never
    # complete-and-ship unconditionally. Absent from older persisted states;
    # resume_delivery defaults to {}.
    conditions: dict = field(default_factory=dict)
    # Fix 1: the last gate agent's structured outcome, retained on the state so
    # a founder/auditor can see WHY a gate did or did not open for review
    # (status, ok, reason). Absent from older persisted states; defaults to {}.
    last_outcome: dict = field(default_factory=dict)
    # Durable G4/G5 release evidence needed to resume safely: verified-build
    # outcome, security/runtime proof summaries, and the final readiness result.
    # Full proof artifacts remain on disk; this state stores the decision inputs
    # that must survive a sidecar restart.
    release_evidence: dict = field(default_factory=dict)


# High-confidence unfilled-template markers that block a gate signature (0.6).
# The noisy single-brace pattern is deliberately excluded so legitimate prose or
# code containing ``{word}`` is not flagged -- only unambiguous leftovers block.
_BLOCKING_PLACEHOLDER_KINDS = frozenset({
    "double-brace", "date-token", "link-token",
    "feature-token", "fill-token", "todo-token",
})


def _artifact_placeholder_violations(path: Path) -> list[str]:
    """Return unambiguous unresolved-template markers in a gate artifact."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    from ..profiles.validation import find_unresolved_placeholders
    out: list[str] = []
    for finding in find_unresolved_placeholders(content):
        if finding.get("kind") in _BLOCKING_PLACEHOLDER_KINDS:
            out.append(f"line {finding.get('line')}: {finding.get('token')!r}")
    return out


def _default_sign(repo_root: Path, gate: str, signer: str, role: str,
                  verdict: str, conditions: str,
                  project_id: str = "default",
                  delivery_run_id: str = "") -> list:
    """Production signing path - INV-3: sign.py is the ONLY signer.

    Writes the audit trail (T38) and co-signs gates whose artifacts require
    different roles (e.g. G3) by signing each artifact with an authorised role.
    *project_id* routes artifact paths through the shared §3.2 governance
    resolver so the signature lands where this delivery's inspect()/status
    reads (default: workspace root, byte-identical)."""
    from .. import artifacts
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    wave = _current_wave_id(repo_root, project_id)
    expected = artifacts.resolve_gate_artifacts(repo_root, gate, project_id=project_id)
    present = [a for a in expected if a.path.is_file()]
    # Fail-closed (0.1 + Fix 2): a gate that declares required artifacts cannot
    # be signed until EVERY one of them exists on disk. The prior check only
    # fail-closed on ZERO present, so a gate signed once >=1 artifact existed
    # -- a founder could advance G0 with just 1 of its 4 required artifacts.
    # Requiring ALL present closes that fail-open for every gate (a custom
    # sign_fn seam still bypasses this, by design). Gates with no declared
    # artifacts are unaffected.
    missing = [a for a in expected if not a.path.is_file()]
    if expected and missing:
        raise ValueError(
            f"gate {gate} cannot be signed: {len(missing)} of its "
            f"{len(expected)} required artifact(s) missing on disk ("
            + ", ".join(a.rel_path for a in missing) + ")"
        )
    # Content check (0.6): a present artifact that is still unfilled template
    # boilerplate is not signable -- a valid hash over placeholder text is not a
    # valid artifact. Reuses the existing placeholder scanner.
    for a in present:
        stale = _artifact_placeholder_violations(a.path)
        if stale:
            raise ValueError(
                f"gate {gate} artifact {a.rel_path} has unresolved template "
                f"placeholders: {'; '.join(stale[:5])}"
            )
    if present and all(role in a.required_roles for a in present):
        return sign.sign_gate(repo_root, gate, signer, role, verdict,
                              conditions, audit_log=audit_log, wave=wave,
                              project_id=project_id, finalize_release=False,
                              delivery_run_id=delivery_run_id)
    signed: list[str] = []
    roles = [role]
    roles.extend(
        required
        for artifact in present
        for required in artifact.required_roles
        if required not in roles
    )
    for authorized_role in roles:
        signed.extend(sign.sign_gate(
            repo_root, gate, signer, authorized_role, verdict, conditions,
            audit_log=audit_log, wave=wave, project_id=project_id,
            finalize_release=False, delivery_run_id=delivery_run_id,
        ))
    return signed


class _CriticChat:
    """Adapts a ProviderAdapter's chat(messages, model, ...) into the simple
    chat(messages) interface briefs.author_brief expects."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def chat(self, messages):
        return self._adapter.chat(messages=messages, model=getattr(self._adapter, "model", ""))


def _current_wave_id(repo_root: Path, project_id: str = "default") -> str | None:
    try:
        from ..status import get_wave_status

        wave = str(
            get_wave_status(repo_root, project_id=project_id).get("wave_id") or ""
        ).strip()
    except Exception:
        return None
    return None if not wave or wave == "\u2014" else wave


# ---------------------------------------------------------------------------
# Per-(repo_root, project_id) single-active-delivery lock (crash-safe).
#
# One operator who starts the SAME project's delivery twice at once must not be
# able to corrupt shared gate state. The lock is keyed on (repo_root,
# project_id) and lives UNDER the project namespace
# (.signalos/projects/<project_id>/delivery.lock): DIFFERENT projects already
# keep isolated state, so the lock can NEVER block two different projects -- it
# only blocks a SECOND concurrent delivery on the SAME project. A lock is
# honoured only while its owning process is still alive AND it is younger than
# the TTL; a killed process (dead pid) or an abandoned lock (past the TTL) is
# reclaimed, so a crash never deadlocks the project permanently.
# ---------------------------------------------------------------------------
_LOCK_TTL_SECONDS = 6 * 3600  # a lock older than this is stale -> reclaimable


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "?"


def _parse_iso_utc(value: Any) -> Optional[datetime]:
    """Parse the ISO-8601 UTC timestamp a delivery lock carries back into an
    aware UTC datetime, or None when absent/unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
            else dt.astimezone(timezone.utc))


def _pid_is_alive_windows(pid: int) -> bool:
    """Windows liveness probe. os.kill(pid, 0) is NOT usable here -- CPython
    maps it to TerminateProcess and would KILL a live process -- so probe with a
    READ-ONLY OpenProcess handle instead. Explicit argtypes/restype are required
    so 64-bit HANDLEs are not truncated on 64-bit Python (a truncated handle
    would make a live process look dead). Fails toward 'alive' on any probe
    error so a still-running delivery is never wrongly reclaimed."""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access-denied means the process EXISTS (alive, just not ours);
            # any other failure (invalid parameter) means no such pid (dead).
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True


def _pid_is_alive(pid: Any) -> bool:
    """Best-effort, CROSS-PLATFORM 'is this process still running?'.

    Fails toward "alive" whenever UNSURE (so a still-running delivery is never
    wrongly reclaimed) and reports "dead" only on a CLEAR no-such-process --
    which a same-host, different, dead pid IS, so its stale lock is reclaimable.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if sys.platform == "win32":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)  # POSIX signal 0 delivers nothing; only checks existence
    except ProcessLookupError:
        return False           # ESRCH -- clearly gone -> reclaimable
    except PermissionError:
        return True            # exists but not ours -> alive
    except OSError:
        return True            # uncertain -> assume alive (never reclaim)
    return True


class GateOrchestrator:
    def __init__(
        self,
        repo_root: Path,
        adapter: Any,
        emit: Callable[[dict], None],
        *,
        enforcement_provider: Optional[EnforcementProvider] = None,
        signer: str = "foundry-agent",
        sign_fn: Optional[Callable[..., list]] = None,
        project_id: str = "default",
        max_rework: int | None = None,
        max_rejections: int = 2,
        max_reopens: int | None = None,
        run_id: Optional[str] = None,
        prompt: str = "",
        critic_adapter: Any = None,
        finalize_closeout: bool = True,
        profile: str = DEFAULT_PROFILE,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.adapter = adapter
        # Engine profile (see PROFILE_STAGES). Callers may deliberately choose
        # the deterministic benchmark default, but an explicit unknown value is
        # never allowed to fail open into that less strict profile.
        self.profile = validate_orchestrator_profile(profile)
        # 1.3 + 1.8: an optional second adapter used to author the plain-words
        # gate brief. When configured (and its vendor differs), the brief is
        # genuinely cross-vendor (1.4's independence guarantee). When absent,
        # falls back to the primary adapter -- still a real 4-field brief,
        # honestly recorded as same-vendor in its provenance.
        self.critic_adapter = critic_adapter
        # Convergence with run_delivery's CLOSEOUT phase (Claim 2): when the
        # walk COMPLETES, produce the SAME closeout evidence run_delivery writes
        # via the shared closeout.* functions, so a fix to closeout reaches BOTH
        # engines. Runs strictly AFTER G5 (post-G4) and only writes .signalos
        # governance evidence, so it cannot change the G4 build. Opt-out flag so
        # a driver (e.g. a build benchmark) that wants zero completion writes can
        # disable it; see _finalize_closeout.
        self.finalize_closeout = finalize_closeout
        self.emit = emit
        self.cancel_check = cancel_check or (lambda: False)
        self.enforcement_provider = enforcement_provider
        self.signer = signer
        self.project_id = validate_project_id(project_id)
        # Lock ownership belongs to this concrete orchestrator instance, not
        # merely to its run id or process.  Two reconstructed objects can share
        # run_id/pid/host while only one is allowed to mutate product/Git state.
        self._delivery_lock_owner_token = uuid.uuid4().hex
        # §3.2: bind the delivery's project namespace into the default sign
        # path so signatures land under the SAME governance dir inspect()
        # reads. Custom sign_fn callables (tests, IPC overrides) keep their
        # historical 6-arg signature untouched.
        if sign_fn is not None:
            self._sign = sign_fn
        else:
            self._sign = _default_sign
        # Fix 1: the production sign path (_default_sign, sign_fn is None) is
        # where governance enforcement lives, so it also enforces the
        # agent-outcome gate -- apply_verdict refuses to approve a gate whose
        # agent did not actually complete reviewable work. An injected custom
        # sign_fn (tests / alternative signers / IPC overrides) owns its own
        # gating, exactly as it already bypasses _default_sign's all-artifacts
        # and placeholder checks. In production _DELIVERY_SIGN_FN is None, so
        # the gate is active on the real desktop/CLI delivery path.
        self._enforce_outcome_gate = sign_fn is None
        # A custom signer historically owns fresh-run outcome gating.  Resume
        # is different: a durable blocked checkpoint must remain unsignable
        # even when the reconstructing surface injects a signer callback.
        self._enforce_blocked_checkpoint = self._enforce_outcome_gate
        self.max_rework = resolve_gate_rework_budget(max_rework)
        self.max_rejections = max_rejections
        self.max_reopens = resolve_gate_reopen_budget(max_reopens)
        # run_id must be unique per delivery - it keys the persisted state dir
        # (.signalos/agent-runs/<run_id>/delivery.json). An explicit run_id is
        # honored verbatim (resume path); the fallback adds a timestamp + uuid
        # suffix so two deliveries with the same prompt prefix never collide.
        rid = validate_run_id(run_id or (
            f"delivery-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        ))
        self.state = DeliveryState(run_id=rid, prompt=prompt,
                                   project_id=project_id,
                                   profile=self.profile,
                                   signer=self.signer,
                                   current_gate=self._current_gate())
        if sign_fn is None:
            self._sign = functools.partial(
                _default_sign,
                project_id=self.project_id,
                delivery_run_id=self.state.run_id,
            )
        # Seed the signed set from the repo's ACTUAL on-disk signatures so a
        # fresh or resumed orchestrator that reaches G4 with G0-G3 already signed
        # KNOWS they are signed. Without this the in-memory signed set is empty,
        # and the AgentLoop's plan-gating / gate-gating see zero signed gates and
        # DENY every product-source implementation write at G4 -- the build agent
        # writes real source, governance throws it away, and only the (allowed)
        # test files survive: the "tests without code" failure. The signed set is
        # derived from next_gate (the first unsigned gate) since the walk signs
        # gates strictly in order; a continuous walk still starts empty at G0 and
        # accumulates as normal.
        if not self.state.signed:
            self.state.signed = self._signed_gates_on_disk()
        # G4 build verification (INV-2 hard completion): the walk must never
        # sign a stub. Set after G4's AgentLoop runs; apply_verdict refuses to
        # sign G4 until this is ok. None = not yet verified.
        self._g4_verify: Optional[dict] = None
        # Fix 1: the most recent gate agent's structured outcome (LoopResult or
        # build result), retained so the outcome gate can be inspected/audited.
        self._last_result: Any = None
        self._gate_in_flight: bool = False
        # Fix 1: wall-clock at which the current gate run started, used to tell
        # a freshly-written (current-run) required artifact from a stale one.
        self._gate_run_started_at: float = 0.0
        # Fix 4 (production profile): the last runtime proof outcome, consulted
        # by the G5 release verifier so a production delivery is not reported
        # ready-to-ship without passing runtime proof.
        self._last_runtime_ok: bool = False

    def _current_gate(self) -> str:
        try:
            insp = wave_engine.inspect(self.repo_root, self.project_id)
            return insp.get("next_gate") or "G0"
        except Exception:
            return "G0"

    def _signed_gates_on_disk(self) -> list:
        """The gates already signed in the repo, as ordered gate labels
        (['G0','G1',...]). Derived from next_gate (the first unsigned gate) since
        the walk signs strictly in order; falls back to all-signed / empty."""
        try:
            insp = wave_engine.inspect(self.repo_root, self.project_id)
        except Exception:
            return []
        if insp.get("all_signed"):
            return list(GATE_ORDER)
        nxt = insp.get("next_gate")
        if nxt in GATE_ORDER:
            return list(GATE_ORDER[:GATE_ORDER.index(nxt)])
        return []

    def _next_after(self, gate: str) -> Optional[str]:
        idx = GATE_ORDER.index(gate)
        return GATE_ORDER[idx + 1] if idx + 1 < len(GATE_ORDER) else None

    def _role_for(self, gate: str) -> str:
        return GATE_ROLES.get(gate, "PO")

    def _g4_build_directive(self, base: str) -> str:
        """The BUILD gate's contract, stated stack- and layout-agnostically.

        The subagent-driven build (subagent_build.py) resolves the signed
        artifacts via the gate-artifact manifest and the stack's commands via
        the adapter registry itself, so this directive carries NO paths, NO
        stack commands, and NO inlined artifact text -- just the gate's
        non-negotiable outcome."""
        directive = (
            "You are at the Build gate. Implement the product for REAL: the "
            "signed plan's acceptance tests are the spec, and this gate cannot "
            "be signed until the product actually builds and its tests pass "
            "green on an independent run. Writing evidence without real, "
            "working implementation is a stub and will be refused."
        )
        return "\n".join([base, "", directive])

    def _gate_message(self, gate: str) -> str:
        base = self.state.prompt or "Proceed with the delivery."
        if gate == "G4":
            base = self._g4_build_directive(base)
        if gate != self.state.current_gate:
            return base
        cyc = self.state.rework.get(gate, 0)
        rej = self.state.rejections.get(gate, 0)
        reo = self.state.reopens.get(gate, 0)
        if not cyc and not rej and not reo:
            return base
        entries = [e for e in self.state.feedback.get(gate, [])
                   if str(e.get("feedback") or "").strip()]
        if not entries:
            # Legacy persisted state (pre-feedback field) or an empty feedback
            # string: keep the old generic nudge rather than fabricating text.
            return base + f"\n\n[Rework cycle {cyc or rej or reo} - address the prior feedback.]"
        latest = entries[-1]
        if latest.get("verdict") == "reject":
            header = (f"[Rejection {rej} - the reviewer rejected the previous "
                      "output; regenerate it, addressing this feedback:]")
        elif latest.get("verdict") == "reopen":
            header = (f"[Reopen {reo} - the reviewer reopened this previously "
                      "signed gate; rework it, addressing this reason:]")
        else:
            header = f"[Rework cycle {cyc} - the reviewer requested these changes:]"
        parts = [base, header + "\n" + str(latest.get("feedback"))]
        prior = entries[:-1]
        if prior:
            parts.append("[Feedback from earlier review cycles:]\n"
                         + "\n".join(f"- {e.get('feedback')}" for e in prior))
        return "\n\n".join(parts)

    def _record_review(self, gate: str, verdict: str, feedback: str, cycle: int,
                       *, store: bool = True) -> None:
        """Store reviewer feedback in the delivery state and append the same
        gate_review audit event the standalone verdict path writes (one audit
        format for both codepaths)."""
        if store:
            self.state.feedback.setdefault(gate, []).append(
                {"verdict": verdict, "cycle": cycle, "feedback": feedback})
        try:
            from .gate_review import record_review_event
            audit_verdict = {"request-changes": "REQUEST-CHANGES",
                             "reject": "REJECTED"}.get(verdict, verdict.upper())
            record_review_event(self.repo_root, gate, audit_verdict, feedback, cycle)
        except OSError:
            pass

    def _run_gate(self, gate: str) -> Any:
        self.state.current_gate = gate
        try:
            agent = agent_loader.load_agent(gate)
            system_prompt = agent.get("content") or (
                f"You are the {GATE_SPECIALISTS[gate]} for gate {gate}."
            )
        except KeyError as exc:
            self.emit({"type": "error", "error": f"Unknown gate {gate}: {exc}"})
            raise
        signed_ints = [
            int(str(g).lstrip("G"))
            for g in self.state.signed
            if str(g).lstrip("G").isdigit()
        ]
        executor = self._gate_executor(gate)
        # Fix 1: mark run start BEFORE the agent runs so we can tell an artifact
        # written THIS run from a stale pre-existing one.
        self._gate_run_started_at = time.time()
        self._gate_in_flight = True
        try:
            result = executor(gate, system_prompt, signed_ints)
        finally:
            self._gate_in_flight = False
        self._last_result = result
        if str(getattr(result, "status", "") or "") == "cancelled":
            reason = str(getattr(result, "error", "") or "delivery cancelled")
            self.state.status = "cancelled"
            self.state.last_outcome = {
                "gate": gate,
                "ok": False,
                "reason": reason,
                "loop_status": "cancelled",
            }
            self._persist()
            self.emit({"type": "cancelled", "run_id": self.state.run_id,
                       "gate": gate, "reason": reason})
            self._release_delivery_lock()
            return result
        if gate == "G4" and isinstance(self._g4_verify, dict):
            # A restart while G4 is awaiting review must retain the independent
            # build verification instead of making the gate unverifiable or,
            # worse, reconstructing evidence from a different run.
            self.state.release_evidence["g4_verify"] = dict(self._g4_verify)
        if gate == "G3":
            self._emit_preview(gate)
        self.emit({
            "type": "gate",
            "gate": gate,
            "title": f"{GATE_SPECIALISTS[gate]} - {gate}",
            "question": GATE_QUESTIONS[gate],
            "specialist": GATE_SPECIALISTS[gate],
        })
        self._emit_brief(gate)
        self._emit_completeness(gate)
        # Fix 1: CONSUME the agent's structured outcome. The gate only opens for
        # a verdict ("awaiting-verdict") when the agent actually COMPLETED the
        # work AND produced this gate's required artifacts this run (and, for
        # G2, an executable/testable plan). A refusal/error/stall/budget-
        # exhaustion/no-file outcome parks the gate as "blocked" instead -- it
        # is NOT presented for approval. Previously the LoopResult was discarded
        # and every gate opened for review unconditionally.
        outcome = self._gate_review_ready(gate, result)
        self.state.last_outcome = {
            "gate": gate,
            "ok": bool(outcome.get("ok")),
            "reason": outcome.get("reason", ""),
            "loop_status": str(getattr(result, "status", "") or ""),
        }
        failure_type = str(getattr(result, "failure_type", "") or "").strip()
        if failure_type:
            self.state.last_outcome["failure_type"] = failure_type
            self.state.last_outcome["error"] = str(
                getattr(result, "error", "") or outcome.get("reason", "")
            )
        if outcome.get("ok"):
            self.state.status = "awaiting-verdict"
        else:
            self.state.status = "blocked"
            # Surfaced as a dedicated NON-error event so callers that assert
            # "no error events" (deterministic smoke walks) still hold, while
            # the UI/founder learns the gate is not reviewable and why.
            self.emit({"type": "gate_blocked", "gate": gate,
                       "reason": outcome.get("reason", "")})
        self._persist()
        return result

    def _gate_executor(self, gate: str):
        """Gate execution strategy: which runner executes this gate's work.
        Default is a single governed AgentLoop; the BUILD gate uses the
        subagent-driven, test-first build. Registered here (not inline in
        _run_gate) so adding a gate-specific executor never edits the walk."""
        executors = {
            "G4": self._execute_build_gate,
        }
        return executors.get(gate, self._execute_default_gate)

    def _execute_default_gate(self, gate: str, system_prompt: str,
                              signed_ints: list) -> Any:
        loop = AgentLoop(
            adapter=self.adapter,
            repo_root=self.repo_root,
            enforcement_provider=self.enforcement_provider,
            run_id=self.state.run_id,
            emit=self.emit,
            cancel_check=self.cancel_check,
            execution_context="delivery",
            active_gate=gate,
            # §3.2 creation side: the loop rebases gate-artifact writes
            # (core/governance|strategy|execution/**) under this project's
            # governance base, so the artifact this gate generates is the
            # one _default_sign/inspect/status resolve at sign time.
            project_id=self.project_id,
            signed_gates=signed_ints,
        )
        return loop.run(system_prompt, self._gate_message(gate))

    def _execute_build_gate(self, gate: str, system_prompt: str,
                            signed_ints: list) -> Any:
        """The BUILD gate runs the SUBAGENT-DRIVEN, test-first build assembled
        from the bundled skills (implementer + spec-compliance reviewer +
        code-quality reviewer per plan task) rather than one free-form loop
        that batches tests and never implements. Reviews run on the
        independent critic_adapter when configured (cross-vendor, not
        double-biased self-review). The _verify_g4_build hard wall is
        unchanged: it still runs the REAL build+test on disk and refuses to
        sign a stub."""
        # Scaffold-first (FIX 2): before the build runs, materialize the
        # SELECTED stack's shell on a greenfield repo so G4 is not deadlocked
        # bootstrapping the root entry/config files governance denies. Strictly
        # idempotent -- a no-op when the shell already exists on disk.
        self._scaffold_shell_if_greenfield()
        # C5: baseline AFTER framework scaffolding but BEFORE build-agent work.
        # Scaffold bytes are infrastructure, not evidence that G4 implemented
        # product value. Reuse an in-flight checkpoint after a crash so partial
        # work remains bound to the interrupted attempt's original baseline.
        try:
            self._prepare_g4_attribution()
        except Exception as exc:
            # Verification below fails closed when no trustworthy baseline
            # exists. Surface the checkpoint failure before any review.
            self.emit({
                "type": "system",
                "text": ("G4 attribution checkpoint failed; this build "
                         f"cannot be signed ({type(exc).__name__}: {exc})."),
            })
        from .subagent_build import BuildCancelled, run_subagent_driven_build
        try:
            result = run_subagent_driven_build(
                self.repo_root,
                self.adapter,
                reviewer_adapter=self.critic_adapter,
                enforcement_provider=self.enforcement_provider,
                emit=self.emit,
                project_id=self.project_id,
                signed_gates=signed_ints,
                prompt=self._gate_message(gate),
                governance_frame=system_prompt,
                parent_run_id=self.state.run_id,
                cancel_check=self.cancel_check,
            )
        except BuildCancelled as exc:
            result = LoopResult(
                run_id=self.state.run_id,
                status="cancelled",
                final_text=str(exc),
                tool_calls_made=0,
                messages=[],
                error=str(exc),
            )
        # INV-2: independently verify the build gate produced a real, passing
        # product before it can be signed. apply_verdict refuses to sign until ok.
        self._g4_verify = self._verify_g4_build(result)
        if not self._g4_verify.get("ok"):
            self.emit({"type": "system",
                       "text": f"G4 build not verified yet: {self._g4_verify.get('reason', '')}"})
        return result

    def _scaffold_shell_if_greenfield(self) -> None:
        """FIX 2: materialize the SELECTED stack's shell before the BUILD gate
        on a GREENFIELD repo, so G4 does not deadlock-by-policy trying to
        bootstrap the root entry/config files governance denies. Strictly
        IDEMPOTENT and SAFE:

          * Resolves the stack from the (profile.json-aware) ``detect_profile``,
            then scaffolds via that profile's adapter.
          * NO-OP when the shell already exists on disk (marker files present):
            it never re-scaffolds and never overwrites an already-scaffolded
            repo. The benchmark fixture ships package.json + a full React/Vitest
            shell, so this does NOTHING there.
          * NO-OP for profiles without a materializable framework shell
            (generic / existing-repo / agent-selected) or an unknown profile.
          * Never raises: a scaffold hiccup is reported and swallowed, so it can
            never fail the walk.
        """
        try:
            from .stacks import (
                adapter_has_greenfield_shell,
                detect_profile,
                get_adapter,
                stack_shell_present,
            )
        except Exception:
            return
        try:
            profile = detect_profile(self.repo_root)
            if not adapter_has_greenfield_shell(profile):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: profile '{profile}' has no stack "
                                   "shell to materialize -- skipped."})
                return
            if stack_shell_present(self.repo_root):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: the '{profile}' shell already exists "
                                   "on disk -- skipped (no files changed)."})
                return
            adapter = get_adapter(profile)
            scaffold = getattr(adapter, "scaffold", None)
            if not callable(scaffold):
                self.emit({"type": "system",
                           "text": f"Scaffold-first: adapter '{profile}' provides no "
                                   "scaffold -- skipped."})
                return
            intent = {"product_name": self.repo_root.name, "prompt": self.state.prompt}
            result = scaffold(self.repo_root, intent)
            created = result.get("created", []) if isinstance(result, dict) else []
            self.emit({"type": "progress",
                       "gate": "G4",
                       "text": f"Scaffolded the {profile} stack shell before build "
                               f"({len(created)} file(s)).",
                       "created": list(created)})
        except Exception as exc:  # never fail the walk on a scaffold hiccup
            self.emit({"type": "system",
                       "text": f"Scaffold-first skipped ({type(exc).__name__}: {exc})."})

    # -- G4 current-run attribution (C5) ------------------------------------

    _G4_ATTRIBUTION_VERSION = 4
    _G4_SOURCE_SUFFIXES = frozenset((*CODE_SUFFIXES, ".css", ".scss", ".sass",
                                    ".less", ".html", ".htm"))
    _G4_EXCLUDED_DIRS = frozenset({
        ".git", ".signalos", "core", "node_modules", "dist", "build", "coverage",
        ".next", ".nuxt", "target", "vendor", "__pycache__",
    })
    _G4_RELEASE_EXCLUDED_DIRS = frozenset({
        ".git", ".signalos", "node_modules", "vendor", ".venv", "venv",
        "dist", "build", "coverage", "target", ".next", ".nuxt", "out",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".tox", ".cache", ".turbo",
    })

    def _g4_attribution_path(self) -> Path:
        return self._state_dir() / "g4-attribution.json"

    @staticmethod
    def _g4_tree_digest(tree: dict[str, str]) -> str:
        return release_tree_digest(tree)

    def _g4_product_tree(self) -> dict[str, str]:
        """Content-hash meaningful product source, excluding tests, build
        output, governance, and symlinks that could escape the workspace."""
        root = self.repo_root.resolve()
        try:
            src = (self.repo_root / self._product_source_dir()).resolve()
            src.relative_to(root)
        except (OSError, ValueError):
            return {}
        if not src.is_dir():
            return {}

        tree: dict[str, str] = {}
        for path in sorted(src.rglob("*")):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                rel = resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            parts_lower = {part.lower() for part in rel.parts}
            if parts_lower & self._G4_EXCLUDED_DIRS:
                continue
            name = path.name.lower()
            if ("tests" in parts_lower or "test" in parts_lower
                    or "__tests__" in parts_lower
                    or ".test." in name or ".spec." in name
                    or "_test." in name or name.startswith("test_")):
                continue
            if path.suffix.lower() not in self._G4_SOURCE_SUFFIXES:
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            tree[str(rel).replace("\\", "/")] = digest
        return tree

    @staticmethod
    def _g4_normalize_meaningful_source(text: str) -> str:
        """A conservative cross-stack normalization for attribution.

        Whitespace-only and whole-line-comment-only edits are not delivery
        evidence. Inline comments are retained to avoid mis-parsing strings or
        language-specific syntax; mechanical build/tests remain the semantic
        authority after this cheap anti-trivial-write check.
        """
        kept: list[str] = []
        in_block_comment = False
        for line in text.splitlines():
            stripped = line.strip()
            if in_block_comment:
                if "*/" in stripped or "-->" in stripped:
                    in_block_comment = False
                continue
            if not stripped:
                continue
            if stripped.startswith(("//", "#", "*")):
                continue
            if stripped.startswith(("/*", "<!--")):
                if not ("*/" in stripped or "-->" in stripped):
                    in_block_comment = True
                continue
            kept.append(re.sub(r"\s+", "", line))
        return "".join(kept)

    def _g4_meaningful_tree(self) -> dict[str, str]:
        """Normalized source tree used only to decide whether G4 made a
        meaningful change. Exact byte hashes remain the release binding."""
        tree: dict[str, str] = {}
        for rel_path in self._g4_product_tree():
            path = self.repo_root / rel_path
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            normalized = self._g4_normalize_meaningful_source(text)
            if not normalized:
                continue
            tree[rel_path] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return tree

    def _g4_release_tree(self) -> dict[str, str]:
        """Exact-byte tree of every shippable workspace file.

        Unlike the attribution source tree, this includes package manifests,
        lockfiles, root config, public assets, migrations, and tests: all bytes
        that ``git add -A`` can ship. It excludes only VCS/control-plane state,
        dependency trees, and generated build/cache directories. Root ``core``
        is SignalOS governance (G4/G5 signatures legitimately change it after
        verification), not product payload. Unreadable files fail closed.
        """
        return workspace_release_tree(self.repo_root)

    def _load_g4_attribution(self) -> Optional[dict]:
        try:
            data = json.loads(self._g4_attribution_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("version") != self._G4_ATTRIBUTION_VERSION:
            return None
        if str(data.get("run_id") or "") != self.state.run_id:
            return None
        if str(data.get("project_id") or "default") != self.project_id:
            return None
        return data

    def _write_g4_attribution(self, data: dict) -> None:
        """Atomically persist protected attribution evidence."""
        path = self._g4_attribution_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _prepare_g4_attribution(self) -> dict:
        """Persist the pre-build tree. Reuse only an interrupted attempt from
        this delivery; completed/refused attempts receive a fresh baseline."""
        existing = self._load_g4_attribution()
        if existing and existing.get("phase") == "running":
            baseline = existing.get("baseline_tree")
            meaningful = existing.get("baseline_meaningful_tree")
            if (isinstance(baseline, dict)
                    and isinstance(meaningful, dict)
                    and existing.get("baseline_digest") == self._g4_tree_digest(baseline)
                    and existing.get("baseline_meaningful_digest")
                    == self._g4_tree_digest(meaningful)
                    and existing.get("source_dir") == self._product_source_dir()):
                return existing

        attempt = 1
        if existing:
            try:
                attempt = max(1, int(existing.get("attempt", 0)) + 1)
            except (TypeError, ValueError):
                attempt = 1
        baseline = self._g4_product_tree()
        meaningful = self._g4_meaningful_tree()
        record = {
            "version": self._G4_ATTRIBUTION_VERSION,
            "run_id": self.state.run_id,
            "project_id": self.project_id,
            "attempt": attempt,
            "phase": "running",
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_dir": self._product_source_dir(),
            "baseline_tree": baseline,
            "baseline_digest": self._g4_tree_digest(baseline),
            "baseline_meaningful_tree": meaningful,
            "baseline_meaningful_digest": self._g4_tree_digest(meaningful),
        }
        self._write_g4_attribution(record)
        return record

    @staticmethod
    def _g4_result_field(result: Any, name: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    def _evaluate_g4_attribution(self, agent_result: Any) -> dict:
        """Require a completed build result and a current-attempt source delta.
        Both accepted and refused outcomes are persisted for audit/resume."""
        status = str(self._g4_result_field(agent_result, "status", "") or "")
        wrote_no_files = bool(self._g4_result_field(
            agent_result, "wrote_no_files", False))
        record = self._load_g4_attribution()

        def refuse(reason: str) -> dict:
            if record is not None:
                record.update({
                    "phase": "refused",
                    "result": {
                        "status": status,
                        "wrote_no_files": wrote_no_files,
                        "tool_calls_made": self._g4_result_field(
                            agent_result, "tool_calls_made", None),
                        "error": self._g4_result_field(agent_result, "error", None),
                    },
                    "verification": {"ok": False, "reason": reason},
                })
                try:
                    self._write_g4_attribution(record)
                except OSError:
                    pass
            return {"ok": False, "reason": reason}

        if status != "completed":
            return refuse("G4 build agent did not complete successfully "
                          f"(outcome: {status or 'missing'})")
        if wrote_no_files:
            return refuse("G4 build agent reported that it wrote no files this run")
        if record is None or record.get("phase") != "running":
            return refuse("G4 has no trustworthy pre-build attribution checkpoint")
        baseline = record.get("baseline_tree")
        if not isinstance(baseline, dict):
            return refuse("G4 pre-build attribution checkpoint is malformed")
        if record.get("baseline_digest") != self._g4_tree_digest(baseline):
            return refuse("G4 pre-build attribution checkpoint failed its tree-hash check")
        baseline_meaningful = record.get("baseline_meaningful_tree")
        if not isinstance(baseline_meaningful, dict):
            return refuse("G4 pre-build meaningful-source checkpoint is malformed")
        if (record.get("baseline_meaningful_digest")
                != self._g4_tree_digest(baseline_meaningful)):
            return refuse("G4 meaningful-source checkpoint failed its tree-hash check")
        if record.get("source_dir") != self._product_source_dir():
            return refuse("G4 product source directory changed during the build")

        post = self._g4_product_tree()
        post_meaningful = self._g4_meaningful_tree()
        changed = sorted(path for path in set(baseline) | set(post)
                         if baseline.get(path) != post.get(path))
        meaningful_changed = sorted(
            path for path in set(baseline_meaningful) | set(post_meaningful)
            if baseline_meaningful.get(path) != post_meaningful.get(path)
        )
        meaningful_written = [path for path in meaningful_changed
                              if path in post_meaningful]
        written = [path for path in changed if path in post]
        removed = [path for path in changed if path not in post]
        record.update({
            "phase": "attributed" if changed else "refused",
            "result": {
                "status": status,
                "wrote_no_files": wrote_no_files,
                "tool_calls_made": self._g4_result_field(
                    agent_result, "tool_calls_made", None),
                "error": self._g4_result_field(agent_result, "error", None),
            },
            "post_tree": post,
            "post_digest": self._g4_tree_digest(post),
            "post_meaningful_tree": post_meaningful,
            "post_meaningful_digest": self._g4_tree_digest(post_meaningful),
            "changed_product_source": changed,
            "meaningful_product_source_change": meaningful_changed,
            "meaningful_written_product_source": meaningful_written,
            "written_product_source": written,
            "removed_product_source": removed,
        })
        if not meaningful_written:
            reason = ("G4 produced zero meaningful written/modified product source "
                      "from its pre-build baseline (stale green, trivial edits, "
                      "and deletion-only changes are not current-run delivery evidence)")
            record["verification"] = {"ok": False, "reason": reason}
            try:
                self._write_g4_attribution(record)
            except OSError:
                pass
            return {"ok": False, "reason": reason,
                    "changed_product_source": changed,
                    "meaningful_product_source_change": meaningful_changed,
                    "meaningful_written_product_source": []}
        try:
            self._write_g4_attribution(record)
        except OSError as exc:
            return {"ok": False,
                    "reason": f"G4 attribution evidence could not be persisted: {exc}"}
        return {"ok": True, "changed_product_source": changed,
                "meaningful_product_source_change": meaningful_changed,
                "meaningful_written_product_source": meaningful_written,
                "written_product_source": written,
                "removed_product_source": removed}

    def _finish_g4_verification(self, result: dict) -> dict:
        """Seal verification and bind success to the exact post-build tree."""
        record = self._load_g4_attribution()
        if record is None or record.get("phase") != "attributed":
            return {"ok": False,
                    "reason": "G4 current-run attribution was not established"}
        final = dict(result)
        if final.get("ok"):
            current_digest = self._g4_tree_digest(self._g4_product_tree())
            if current_digest != record.get("post_digest"):
                final = {"ok": False,
                         "reason": ("product source changed during independent G4 "
                                    "verification; rerun from a fresh baseline")}
        if final.get("ok"):
            try:
                release_tree = self._g4_release_tree()
            except (OSError, ValueError) as exc:
                final = {"ok": False,
                         "reason": ("could not capture the verified shippable "
                                    f"workspace tree: {type(exc).__name__}: {exc}")}
            else:
                record["release_tree"] = release_tree
                record["release_digest"] = self._g4_tree_digest(release_tree)
        record["phase"] = "verified" if final.get("ok") else "refused"
        record["verified_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        record["verification"] = final
        try:
            self._write_g4_attribution(record)
        except OSError as exc:
            return {"ok": False,
                    "reason": f"G4 verification evidence could not be persisted: {exc}"}
        if final.get("ok"):
            final["attribution"] = {
                "attempt": record.get("attempt"),
                "post_digest": record.get("post_digest"),
                "release_digest": record.get("release_digest"),
                "changed_product_source": list(
                    record.get("changed_product_source") or []),
                "meaningful_product_source_change": list(
                    record.get("meaningful_product_source_change") or []),
                "meaningful_written_product_source": list(
                    record.get("meaningful_written_product_source") or []),
            }
        return final

    def _g4_verification_for_current_tree(self) -> dict:
        """Restore proof only if it is valid for current product bytes."""
        record = self._load_g4_attribution()
        if record is None or record.get("phase") != "verified":
            return {"ok": False,
                    "reason": "no verified current-run G4 attribution evidence"}
        verification = record.get("verification")
        post = record.get("post_tree")
        if not isinstance(verification, dict) or not verification.get("ok"):
            return {"ok": False, "reason": "persisted G4 verification did not pass"}
        if not isinstance(post, dict):
            return {"ok": False, "reason": "persisted G4 product tree is malformed"}
        digest = self._g4_tree_digest(post)
        if digest != record.get("post_digest"):
            return {"ok": False, "reason": "persisted G4 product tree hash is invalid"}
        changed = record.get("changed_product_source")
        meaningful_changed = record.get("meaningful_product_source_change")
        meaningful_written = record.get("meaningful_written_product_source")
        if (not isinstance(changed, list)
                or not isinstance(meaningful_changed, list)
                or not isinstance(meaningful_written, list)
                or not meaningful_written):
            return {"ok": False,
                    "reason": "persisted G4 proof contains no product-source change"}
        if record.get("source_dir") != self._product_source_dir():
            return {"ok": False,
                    "reason": "product source directory no longer matches verified G4"}
        if self._g4_tree_digest(self._g4_product_tree()) != digest:
            return {"ok": False, "reason": "product source changed after G4 verification"}
        release_tree = record.get("release_tree")
        if not isinstance(release_tree, dict):
            return {"ok": False,
                    "reason": "persisted G4 shippable workspace tree is malformed"}
        release_digest = self._g4_tree_digest(release_tree)
        if release_digest != record.get("release_digest"):
            return {"ok": False,
                    "reason": "persisted G4 shippable workspace tree hash is invalid"}
        try:
            current_release_digest = self._g4_tree_digest(self._g4_release_tree())
        except (OSError, ValueError) as exc:
            return {"ok": False,
                    "reason": ("could not re-read shippable workspace files: "
                               f"{type(exc).__name__}: {exc}")}
        if current_release_digest != release_digest:
            return {"ok": False,
                    "reason": "shippable workspace files changed after G4 verification"}
        restored = dict(verification)
        restored["attribution"] = {
            "attempt": record.get("attempt"),
            "post_digest": digest,
            "release_digest": release_digest,
            "changed_product_source": list(changed),
            "meaningful_product_source_change": list(meaningful_changed),
            "meaningful_written_product_source": list(meaningful_written),
        }
        return restored

    def _verify_g4_build(self, agent_result: Any) -> dict:
        """Independently verify G4 built a real, working product — the walk must
        never sign a stub (INV-2, no fake-green). Two checks, both required:

          1. The build actually WROTE real product source this run (not just
             prose Build Evidence over a scaffold stub), checked in the stack
             adapter's OWN source directory.
          2. The product BUILDS and its TESTS PASS on an independent run of the
             stack adapter's own validation plan.

        Returns {"ok": bool, "reason": str, ...}. Never raises — a failure to
        verify is reported as not-ok so signing is refused, not bypassed.
        """
        # Consume the actual build result and establish current-run attribution
        # before a pre-existing green disk can satisfy the mechanical checks.
        attribution = self._evaluate_g4_attribution(agent_result)
        if not attribution.get("ok"):
            return attribution

        def finish(result: dict) -> dict:
            return self._finish_g4_verification(result)

        src_dir = self._product_source_dir()
        # 1. Real product source must EXIST in the repo (checked on disk -- the
        # LoopResult exposes no files list). A scaffold-only stub is refused.
        if not self._repo_has_real_product_src():
            return finish({
                "ok": False,
                "reason": f"No real product source under {src_dir}/** -- implement the "
                          "product's modules/logic (not just tests or a stub), then rebuild.",
            })
        # 1.5. WIRING gate: every product module must be reachable from the app
        # entry through the import graph. Components that exist but are never
        # composed ("pieces without wiring" -- the dominant observed failure)
        # are refused by machine, with the orphan modules named.
        orphans = self._unwired_modules()
        if orphans:
            return finish({
                "ok": False,
                "reason": ("Build has UNWIRED modules -- written but never imported/"
                           "composed into the app. Wire each into the app's component "
                           "tree (import + render/use it), or remove it if truly "
                           "unneeded:\n" + "\n".join(f"- {o}" for o in orphans)),
            })
        # 2. Independent build + test; surface the ACTUAL errors so rework
        # feedback is actionable (e.g. a missing module the compiler can name).
        try:
            from .stacks import detect_profile
            from .validation import build_validation_plan, run_validation
            profile = detect_profile(self.repo_root)
            plan = build_validation_plan(self.repo_root, profile)
            if not (plan.get("can_validate_build") and plan.get("can_validate_tests")):
                return finish({
                    "ok": False,
                    "reason": f"profile '{profile}' has no build/test validation; cannot verify a real build.",
                })
            result = run_validation(self.repo_root, plan)
            results = result.get("results", {})
            b, t = results.get("build", {}), results.get("test", {})
            if b.get("status") == "passed" and t.get("status") == "passed":
                # 3. UX/BEHAVIORAL ACCEPTANCE hard gate (product requirement, so
                # it runs in BOTH profiles): a green build that ships NO real,
                # styled, usable UI is still refused. Measured by rendering the
                # product on jsdom (offline). Blocks only on a genuine measured
                # failure; a build whose UI cannot be measured offline is never
                # false-failed.
                ux = self._verify_ux_acceptance(profile)
                if not ux.get("ok"):
                    return finish({
                        "ok": False,
                        "reason": ux.get("reason", "UX acceptance failed"),
                        "build": "passed", "test": "passed", "ux": "failed",
                    })
                return finish({"ok": True, "profile": profile,
                               "ux": ux.get("status", "ok")})
            # Prefer the parsed per-file diagnostics (crisp + actionable); fall
            # back to raw command output.
            errs = []
            for v in (result.get("violations") or [])[:25]:
                f = v.get("file") or v.get("path") or ""
                ln = v.get("line")
                errs.append(f"{f}{f':{ln}' if ln else ''} {v.get('code','')} {v.get('message','')}".strip())
            detail = "\n".join(errs) if errs else (
                ((b.get("output") or "") + "\n" + (t.get("output") or ""))[-2000:])
            # The rebuild hint quotes the stack's OWN validation commands.
            cmds = " && ".join([*plan.get("build", []), *plan.get("test", [])]) \
                or "the project's build and test commands"
            return finish({
                "ok": False,
                "reason": (f"G4 build is not green. Fix EVERY error below, then rebuild "
                           f"({cmds}). If a test imports a module that does not exist, "
                           f"the implementation file MUST be created under {src_dir}/** "
                           "-- never delete or weaken the test:\n" + detail),
                "build": b.get("status"), "test": t.get("status"),
            })
        except Exception as exc:  # never bypass on error -- fail closed
            return finish({
                "ok": False,
                "reason": f"G4 build verification error: {type(exc).__name__}: {exc}",
            })

    # (Import-graph shapes + entry names live in wiring_check.py -- the single
    # source for the wiring analysis, shared with the in-loop build reviewer so a
    # module written-but-never-composed is caught DURING the build, not only at
    # this gate.)

    def _unwired_modules(self) -> list:
        """Product-source modules UNREACHABLE from the app entry through the
        import graph -- the dominant 'green but not a product' failure (pieces
        written, never composed). Delegates to the shared wiring check, which the
        in-loop build reviewer also runs, so the same violation is caught DURING
        the build (with a fix pass) rather than only here at the gate."""
        return find_unwired_modules(self.repo_root, self._product_source_dir())

    def _verify_ux_acceptance(self, profile: str) -> dict:
        """UX/BEHAVIORAL acceptance HARD gate: a build is not VERIFIED unless it
        ships a REAL, styled, usable UI. The check RENDERS the product entry on
        jsdom (offline -- @testing-library, no Playwright/network) and MEASURES
        the mounted DOM (not source/className counting): interactive controls
        found by ARIA role, real styling signal (component library / inline
        styles / CSS classes on rendered elements), and a11y names.

        Returns ``{"ok": bool[, "reason": str][, "status": str]}``. Blocks ONLY
        on a genuine measured failure (``ran and not ok``); a build whose UI
        cannot be measured offline (non-browser profile, deps not installed, no
        App entry) returns ok so a genuinely-good build is never false-failed on
        tooling grounds. Never raises -- a tooling error is treated as a skip,
        not a UX verdict."""
        try:
            from .acceptance import run_ux_acceptance
            result = run_ux_acceptance(
                self.repo_root,
                source_dir=self._product_source_dir(),
                profile=profile)
        except Exception as exc:  # tooling error is not a UX verdict
            return {"ok": True, "status": f"ux-skip ({type(exc).__name__})"}
        if result.get("ran") and not result.get("ok"):
            return {"ok": False,
                    "reason": result.get("reason", "UX acceptance failed")}
        return {"ok": True,
                "status": "passed" if result.get("ran") else "skipped"}

    def _product_source_dir(self) -> str:
        """The stack adapter's own source directory (e.g. src, app, internal/app
        -- never assume one layout). Falls back to 'src' if resolution fails."""
        try:
            from .stacks import detect_profile, get_adapter
            targets = get_adapter(detect_profile(self.repo_root)).resolve_targets(self.repo_root)
            return str(targets.get("source") or "src").strip() or "src"
        except Exception:
            return "src"

    # Single source lives in wiring_check.py (shared with the in-loop reviewer);
    # referenced here for _repo_has_real_product_src.
    _CODE_SUFFIXES = CODE_SUFFIXES
    _SCAFFOLD_NAMES = SCAFFOLD_NAMES

    def _repo_has_real_product_src(self) -> bool:
        """True iff the repo has real product source beyond the scaffold: a
        non-test code file in the stack's source directory that isn't a known
        scaffold entry file or the placeholder App stub."""
        src = self.repo_root / self._product_source_dir()
        if not src.is_dir():
            return False
        for p in src.rglob("*"):
            if not p.is_file() or p.suffix not in self._CODE_SUFFIXES:
                continue
            n = p.name
            if ".test." in n or ".spec." in n or "_test." in n or n.startswith("test_"):
                continue
            if n in self._SCAFFOLD_NAMES:
                continue
            if n == "App.tsx":
                try:
                    if len(p.read_text(encoding="utf-8", errors="replace").splitlines()) <= 8:
                        continue  # the scaffold's placeholder stub
                except OSError:
                    pass
            return True
        return False

    def _emit_brief(self, gate: str) -> None:
        """1.3 + 1.8: author the real 4-field plain-words brief for this gate's
        artifact via the model router's critique routing -- a cross-vendor critic
        when `critic_adapter` is configured (closing 1.3's routing policy and
        1.4's independence guarantee into the live gate walk), honestly falling
        back to the primary adapter (same-vendor) otherwise. Never blocks the
        gate walk; failures are silent, matching the other advisory emits."""
        try:
            from .. import artifacts as artifacts_mod
            from ..model_router import route
            from .briefs import author_brief, validate_brief

            resolved = [a for a in artifacts_mod.resolve_gate_artifacts(
                            self.repo_root, gate, project_id=self.project_id)
                        if a.path.is_file()]
            if not resolved:
                return
            artifact_content = resolved[0].path.read_text(encoding="utf-8", errors="replace")

            author_model = str(getattr(self.adapter, "model", "") or "")
            candidates = [author_model]
            critic_model = ""
            if self.critic_adapter is not None:
                critic_model = str(getattr(self.critic_adapter, "model", "") or "")
                if critic_model:
                    candidates.append(critic_model)

            chosen_model = route("critique", primary_model=author_model,
                                 available=candidates, author_model=author_model)
            use_critic = bool(critic_model) and chosen_model == critic_model
            reviewer_adapter = self.critic_adapter if use_critic else self.adapter
            reviewer_agent = "Critic" if use_critic else GATE_SPECIALISTS.get(gate, gate)
            reviewer_model = critic_model if use_critic else author_model

            brief = author_brief(
                artifact_content, _CriticChat(reviewer_adapter),
                author_agent=GATE_SPECIALISTS.get(gate, gate), author_model=author_model,
                reviewer_agent=reviewer_agent, reviewer_model=reviewer_model,
                artifact=resolved[0].rel_path,
            )
            payload = brief.to_dict()
            payload["type"] = "brief"
            payload["gate"] = gate
            # Honest self-report: when there's no real critic, independence is
            # not met and validate_brief says so here rather than hiding it.
            payload["contract_violations"] = validate_brief(brief)
            self.emit(payload)
        except Exception:
            pass

    def _emit_completeness(self, gate: str) -> None:
        """1.9: advisory inversion pass -- surface concerns a gate artifact may
        silently not address (identity, permissions, onboarding, data lifecycle,
        ops/failure). Advisory only; never blocks the gate."""
        try:
            from .. import artifacts
            from .completeness import completeness_findings
            for a in artifacts.resolve_gate_artifacts(
                    self.repo_root, gate, project_id=self.project_id):
                if not a.path.is_file():
                    continue
                findings = completeness_findings(
                    a.path.read_text(encoding="utf-8", errors="replace"))
                if findings:
                    self.emit({"type": "completeness", "gate": gate,
                               "artifact": a.rel_path, "findings": findings})
        except Exception:
            pass

    def _emit_incident(self, scenario: str, detail: str = "") -> None:
        """1.10: surface a failure as a plain-words incident card with recovery
        options, never just a bare error/stack trace."""
        try:
            from .incidents import build_incident_card
            self.emit(build_incident_card(scenario, detail=detail).to_dict())
        except Exception:
            pass

    def _emit_preview(self, gate: str) -> None:
        try:
            from .design_preview import generate_design_preview_html
            html = generate_design_preview_html({}, {"prompt": self.state.prompt})
        except Exception:
            html = "<!DOCTYPE html><html><body><main>Design preview</main></body></html>"
        self.emit({"type": "preview", "srcDoc": html, "caption": f"{gate} design preview"})
        # 0.7: run the (previously dormant) agentic UX-friction QA on the preview
        # surface and surface it to the founder at the design gate. Deterministic
        # heuristic pass only -- no network -- so it never blocks the gate.
        try:
            from .ux_friction import heuristic_findings
            findings = heuristic_findings(html)
            self.emit({"type": "ux_friction", "gate": gate,
                       "findings": findings, "count": len(findings)})
        except Exception:
            pass

    # -- agent-outcome gate (Fix 1 + Fix 3) ---------------------------------

    _UNREVIEWABLE_LOOP_STATUSES = _UNREVIEWABLE_LOOP_STATUSES

    def _gate_review_ready(self, gate: str, result: Any) -> dict:
        """Decide whether the gate agent's outcome is a SUCCESSFUL, reviewable
        result, so `_run_gate` opens the gate for a verdict only when the agent
        actually did the work. Returns ``{"ok": bool, "reason": str}``.

        * G4 defers to the independent build verification (`_g4_verify`): the
          build wall already proves a real, passing product this run.
        * Every other gate requires a LoopResult that COMPLETED (not error /
          cancelled / budget-exhausted / stalled / truncated / text-only) and
          actually wrote files this run (not narration-only), PLUS every
          manifest-required artifact present on disk and freshly written this
          run (a stale pre-existing file cannot masquerade as this run's work).
        * G2 additionally requires an executable + testable plan contract
          (`_validate_g2_plan_contract`) -- an Expectation Map alone is not a
          plan the Build gate can drive.
        """
        if gate == "G4":
            g4 = self._g4_verify or {}
            if g4.get("ok"):
                return {"ok": True, "reason": ""}
            return {"ok": False,
                    "reason": g4.get("reason", "G4 build not independently verified")}
        status = str(getattr(result, "status", "") or "")
        if status in self._UNREVIEWABLE_LOOP_STATUSES:
            return {"ok": False,
                    "reason": f"agent did not complete the gate (outcome: {status})"}
        if status and status != "completed":
            return {"ok": False,
                    "reason": f"agent outcome was not a success (outcome: {status})"}
        if getattr(result, "wrote_no_files", False):
            return {"ok": False,
                    "reason": "agent produced no files this run (narration only)"}
        missing, none_fresh = self._required_artifact_state(gate)
        if missing:
            return {"ok": False,
                    "reason": "required artifact(s) missing on disk: " + ", ".join(missing)}
        if none_fresh:
            return {"ok": False,
                    "reason": "no required artifact was written this run (stale outputs) -- "
                              "the agent did not (re)produce the gate's artifacts"}
        if gate == "G2":
            problems = self._validate_g2_plan_contract()
            if problems:
                return {"ok": False,
                        "reason": "G2 plan contract incomplete: " + "; ".join(problems)}
        return {"ok": True, "reason": ""}

    def _required_artifact_state(self, gate: str) -> "tuple[list, bool]":
        """(missing_rel_paths, none_written_this_run) for *gate*'s manifest
        artifacts. ``missing`` lists artifacts not on disk. ``none_written_this_
        run`` is True when every required artifact predates this gate run (mtime
        older than run start, with a small grace for filesystem resolution) --
        i.e. the agent (re)wrote none of them. Never raises."""
        missing: list[str] = []
        fresh = 0
        started = float(getattr(self, "_gate_run_started_at", 0.0) or 0.0)
        try:
            from .. import artifacts
            resolved = artifacts.resolve_gate_artifacts(
                self.repo_root, gate, project_id=self.project_id)
        except Exception:
            return missing, False
        if not resolved:
            return missing, False
        for a in resolved:
            try:
                if not a.path.is_file():
                    missing.append(a.rel_path)
                    continue
                if a.path.stat().st_mtime >= started - 2.0:
                    fresh += 1
            except OSError:
                missing.append(a.rel_path)
        none_fresh = started > 0.0 and not missing and fresh == 0
        return missing, none_fresh

    def _validate_g2_plan_contract(self) -> list:
        """Fix 3 -- the G2 (Plan) gate's real contract: an EXECUTABLE + TESTABLE
        plan, not merely an Expectation Map. Returns a list of human-readable
        problems (empty == satisfied):

          1. ``PLAN.tasks.yaml`` parses into >=1 canonical task (the machine
             plan the Build gate consumes).
          2. ``ACCEPTANCE_CRITERIA.md`` exists (the testable spec).
          3. A rendered ``PLAN.md`` exists and is in parity with the machine
             plan (each task surfaced in the rendered view).
          4. Every task carries an acceptance-test path AND that RED test
             skeleton exists on disk (test-first / TDD-at-plan-time).

        # INTEGRATE: strict validator -- once sign.is_gate_signed_strict lands,
        # the plan-contract check can also be asserted at signature time.
        """
        problems: list[str] = []
        try:
            from .. import artifacts
        except Exception:
            return ["cannot resolve plan artifact paths"]

        def _ws(rel: str) -> Optional[Path]:
            try:
                return artifacts.resolve_workspace_path(
                    self.repo_root, rel, project_id=self.project_id)
            except Exception:
                return None

        acc = _ws("core/execution/ACCEPTANCE_CRITERIA.md")
        plan_md = _ws("core/execution/PLAN.md")
        tasks_yaml = _ws("core/execution/PLAN.tasks.yaml")
        if acc is None or not acc.is_file():
            problems.append("ACCEPTANCE_CRITERIA.md missing")
        if plan_md is None or not plan_md.is_file():
            problems.append("rendered PLAN.md missing")
        try:
            from .subagent_build import decompose_canonical_plan_tasks
            tasks = decompose_canonical_plan_tasks(self.repo_root, self.project_id)
        except Exception:
            tasks = []
        if not tasks:
            if tasks_yaml is None or not tasks_yaml.is_file():
                problems.append("PLAN.tasks.yaml missing (no executable task plan)")
            else:
                problems.append("PLAN.tasks.yaml has no valid tasks")
            return problems  # cannot check RED skeletons without tasks
        plan_text = ""
        if plan_md is not None and plan_md.is_file():
            try:
                plan_text = plan_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                plan_text = ""
        no_test, missing_skel, unrendered = [], [], []
        for t in tasks:
            test = str(getattr(t, "test", "") or "").strip()
            tid = str(getattr(t, "id", "") or "")
            title = str(getattr(t, "name", "") or "")
            if not test:
                no_test.append(tid or title or "?")
                continue
            if not (self.repo_root / test).is_file():
                missing_skel.append(test)
            if plan_text and not (
                    (title and title in plan_text) or (tid and tid in plan_text)):
                unrendered.append(tid or title or "?")
        if no_test:
            problems.append("task(s) without an acceptance test: "
                            + ", ".join(map(str, no_test[:5])))
        if missing_skel:
            problems.append("missing RED test skeleton(s): "
                            + ", ".join(missing_skel[:5]))
        if unrendered:
            problems.append("PLAN.md not in parity with PLAN.tasks.yaml (missing: "
                            + ", ".join(map(str, unrendered[:5])) + ")")
        return problems

    # -- single-active-delivery lock (workspace-global product bytes) -------

    def _delivery_lock_path(self) -> Path:
        """Guard shared product bytes and Git state across virtual projects.

        Governance documents are project-namespaced, but source files, build
        output, Git index/HEAD, and origin are workspace-global.  Until product
        worktrees are physically isolated, concurrent project deliveries would
        attribute and ship each other's writes, so one workspace owns one live
        delivery operation.
        """
        return safe_control_path(
            self.repo_root, ".signalos", "locks", "delivery.lock",
        )

    def _read_delivery_lock(self) -> Optional[dict]:
        try:
            data = json.loads(self._delivery_lock_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _delivery_lock_is_live(self, info: dict) -> bool:
        """A lock still GUARDS the project only while its owning process is
        alive AND it is younger than the TTL. A dead pid, a past-TTL age, or an
        unparseable/absent timestamp is STALE (reclaimable) -- crash-safety must
        never deadlock a project permanently."""
        if not isinstance(info, dict):
            return False
        # A same-host process lock remains live for the life of that process;
        # long deliveries must not silently lose exclusivity after a wall-clock
        # TTL. TTL applies only when the owning host cannot be probed locally.
        if str(info.get("host") or "") == _hostname():
            return _pid_is_alive(info.get("pid"))
        acquired = _parse_iso_utc(info.get("acquired_at"))
        if acquired is None:
            return False
        age = (datetime.now(timezone.utc) - acquired).total_seconds()
        return age < _LOCK_TTL_SECONDS

    def _write_delivery_lock(self) -> bool:
        path = self._delivery_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".delivery-lock-{os.getpid()}-{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("x", encoding="utf-8") as handle:
                json.dump({
                    "run_id": self.state.run_id,
                    "project_id": self.project_id,
                    "owner_token": self._delivery_lock_owner_token,
                    "pid": os.getpid(),
                    "host": _hostname(),
                    "acquired_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"),
                }, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # A hard-link publish is atomic and refuses an existing target;
                # contenders can never observe/truncate a half-written JSON lock.
                os.link(tmp, path)
                return True
            except FileExistsError:
                return False
        except FileExistsError:
            return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _acquire_delivery_lock(self) -> Optional[dict]:
        """Take the workspace-global single-active-delivery lock at the start
        of a delivery. Returns a BLOCKED status dict when another LIVE delivery
        already holds it; otherwise acquires -- reclaiming only a STALE lock
        (dead pid / past TTL) -- and returns None. Re-entry is allowed solely
        for this exact orchestrator instance's random owner token. Run-id,
        process-id, and host equality alone never establish ownership.
        """
        for _attempt in range(3):
            if self._write_delivery_lock():
                return None
            existing = self._read_delivery_lock()
            same_owner = bool(
                existing
                and str(existing.get("run_id") or "") == self.state.run_id
                and str(existing.get("pid") or "") == str(os.getpid())
                and str(existing.get("host") or "") == _hostname()
                and str(existing.get("owner_token") or "")
                == self._delivery_lock_owner_token
            )
            if same_owner:
                return None
            if existing and self._delivery_lock_is_live(existing):
                other = str(existing.get("run_id") or "?")
                pid = existing.get("pid")
                return {
                    "status": "blocked",
                    "gate": self.state.current_gate,
                    "active_run_id": other,
                    "reason": (f"a delivery is already active in this workspace "
                               f"(run {other}, project "
                               f"{existing.get('project_id', 'default')}, pid {pid}) -- "
                               "finish or cancel it first."),
                }
            try:
                self._delivery_lock_path().unlink()
            except FileNotFoundError:
                continue
        return {
            "status": "blocked",
            "gate": self.state.current_gate,
            "reason": "delivery lock could not be acquired atomically",
        }

    def _release_delivery_lock(self) -> None:
        """Release the lock only when its run and random owner token are ours.

        A second object with the same run_id/pid/host is still a contender and
        must never delete the acquiring instance's lock. Never raises; a killed
        process is covered by stale reclaim rather than this path.
        """
        try:
            info = self._read_delivery_lock()
            if (
                info
                and str(info.get("run_id") or "") == self.state.run_id
                and str(info.get("owner_token") or "")
                == self._delivery_lock_owner_token
            ):
                self._delivery_lock_path().unlink()
        except OSError:
            pass

    def start(self) -> dict:
        gate = self.state.current_gate
        blocked = self._acquire_delivery_lock()
        if blocked is not None:
            # A dedicated NON-error event so callers asserting "no error events"
            # still hold, while the UI/founder learns a delivery is already live.
            self.emit({"type": "delivery_blocked", "gate": gate,
                       "reason": blocked["reason"],
                       "active_run_id": blocked.get("active_run_id")})
            return {"run_id": self.state.run_id, "gate": gate,
                    "status": "blocked", "reason": blocked["reason"]}
        try:
            self._run_gate(gate)
        except Exception:
            self._release_delivery_lock()
            raise
        return {"run_id": self.state.run_id, "gate": gate, "status": self.state.status}

    def apply_verdict(self, verdict: str, feedback: str = "") -> dict:
        gate = self.state.current_gate
        v = verdict if verdict in _KNOWN_VERDICTS else _classify(verdict)
        cancelled = self._cancel_at_boundary("verdict")
        if cancelled is not None:
            return cancelled

        if v in ("approve", "approve-with-conditions"):
            g0_preapproved = False
            # A persisted blocked checkpoint is never signable merely because a
            # sidecar restarted or a test injected a custom sign function.  The
            # reviewer must request changes so the gate agent reruns and produces
            # a new reviewable outcome.
            if (self.state.status == "blocked"
                    and self._enforce_blocked_checkpoint):
                reason = ((self.state.last_outcome or {}).get("reason")
                          or "the gate is blocked and not open for review")
                self.emit({"type": "gate_blocked", "gate": gate, "reason": reason})
                return {"status": "not-reviewable", "gate": gate, "reason": reason}
            # INV-2: G4 cannot be signed until its build is independently verified
            # (real product source written + build/tests pass). Never fake-green.
            if gate == "G4" and self._enforce_outcome_gate:
                # Revalidate the protected persisted proof against the current
                # product tree on every production verdict. This restores a
                # legitimate crash-resumed G4 and invalidates source edits made
                # after the independent build check.
                self._g4_verify = self._g4_verification_for_current_tree()
            if gate == "G4" and not (self._g4_verify or {}).get("ok"):
                reason = (self._g4_verify or {}).get("reason", "build not verified")
                self.emit({"type": "error", "error": f"Gate G4 cannot be signed: {reason}"})
                return {"status": "build-not-verified", "gate": gate, "reason": reason}
            # Fix 1 (production sign path): never sign a gate the agent did not
            # actually complete. `_run_gate` only opens a gate for review
            # ("awaiting-verdict") when the agent's outcome was successful and
            # this run's required artifacts (and, for G2, an executable/testable
            # plan) are present; otherwise the gate is "blocked". Refuse to
            # approve a gate that was never opened for review. G4 is gated above
            # by its own build verification. A custom sign_fn (tests /
            # alternative signers) owns its own gating -- see _enforce_outcome_gate.
            if (self._enforce_outcome_gate and gate != "G4"
                    and self.state.status not in ("awaiting-verdict", "reopened")):
                reason = ((self.state.last_outcome or {}).get("reason")
                          or "the gate is not open for review (agent did not "
                             "complete reviewable work)")
                self.emit({"type": "gate_blocked", "gate": gate, "reason": reason})
                return {"status": "not-reviewable", "gate": gate, "reason": reason}
            # Fix 5: an "approve with conditions" needs an actual condition to
            # enforce -- a blank condition is just an approve, and cannot be
            # tracked/resolved. Require the text so unresolved conditions can
            # block readiness below.
            if v == "approve-with-conditions" and not str(feedback or "").strip():
                self.emit({"type": "error",
                           "error": f"Gate {gate}: approve-with-conditions requires a "
                                    "written condition."})
                return {"status": "conditions-need-text", "gate": gate}
            # C7: G5 readiness is a PRECONDITION to signing. The orchestrated
            # signer suppresses release side effects; this verified/pending
            # checkpoint is durable BEFORE the signature and later drives the
            # idempotent seal/commit/push state machine.
            release = None
            if gate == "G5":
                pending = ({"G5": str(feedback or "").strip()}
                           if v == "approve-with-conditions" else None)
                release = self._verify_g5_release(pending_conditions=pending)
                ready = bool(release.get("ok"))
                self._set_release_verification(release)
                if ready and self._enforce_outcome_gate:
                    previous = self._release_finalization_marker()
                    self.state.release_evidence["release_finalization"] = {
                        "schema_version": "signalos.release-finalization.v1",
                        "status": "pending",
                        "phase": "verified",
                        "run_id": self.state.run_id,
                        "project_id": self.project_id,
                        "profile": self.profile,
                        "release_digest": str(release.get("release_digest") or ""),
                        "attempts": int(previous.get("attempts") or 0),
                        "created_at": str(
                            previous.get("created_at") or self._release_timestamp()
                        ),
                        "updated_at": self._release_timestamp(),
                    }
                try:
                    self._persist()
                except OSError as exc:
                    reason = f"release verification evidence could not be persisted: {exc}"
                    self.emit({"type": "error", "error": reason})
                    return {"status": "release-not-ready", "gate": gate,
                            "ready": False, "reasons": [reason]}
                self.emit({"type": "release_verified", "ready": ready,
                           "reasons": release.get("reasons", [])})
                if not ready:
                    reasons = list(release.get("reasons") or [])
                    reason = "; ".join(reasons) or "release verification did not pass"
                    self.emit({"type": "gate_blocked", "gate": gate,
                               "reason": reason})
                    return {"status": "release-not-ready", "gate": gate,
                            "ready": False, "reasons": reasons}
            # Production-profile POST-BUILD release-safety stages. Runs only for
            # the BUILD gate, only after its build is independently verified
            # (above), so it can NEVER change the verified-build outcome, the
            # scores, or the product bytes -- it only adds a release gate on top.
            # In the benchmark profile this is a STRICT no-op (returns None), so
            # the benchmark G4 sign is byte-identical to today.
            if gate == "G4":
                blocked = self._run_post_build_stages()
                if blocked is not None:
                    # A release-stage refusal is a durable non-reviewable state.
                    # Persist it before returning so agent:resume cannot turn it
                    # back into an approvable checkpoint.
                    reason = str(blocked.get("reason") or "release safety stage blocked")
                    self.state.status = "blocked"
                    self.state.last_outcome = {
                        "gate": gate,
                        "ok": False,
                        "reason": reason,
                        "loop_status": str(blocked.get("status") or "blocked"),
                    }
                    self._persist()
                    return blocked
                if any(PROFILE_STAGES[self.profile].values()):
                    # Production proof is a durable release decision input,
                    # not transient process memory.  Checkpoint it before the
                    # signer runs so a signing error or sidecar crash cannot
                    # erase the security/runtime result on resume.
                    self._persist()
            if gate == "G0" and self._enforce_outcome_gate:
                authority = sign.check_gate_signed_strict(
                    self.repo_root, "G0", project_id=self.project_id,
                )
                if not authority.signed:
                    reason = (
                        "Gate G0 requires the exact explicit solo-founder approval "
                        "transaction before a generic verdict can advance it"
                    )
                    self.emit({"type": "gate_blocked", "gate": gate,
                               "reason": reason,
                               "reasons": list(authority.reasons or [])})
                    return {
                        "status": "explicit-approval-required",
                        "gate": gate,
                        "reason": reason,
                        "reasons": list(authority.reasons or []),
                    }
                # The authority transaction already wrote and audit-linked the
                # canonical G0 signatures. Do not append a second raw signature.
                g0_preapproved = True
            cancelled = self._cancel_at_boundary("gate signature")
            if cancelled is not None:
                return cancelled
            if not g0_preapproved:
                try:
                    self._sign(self.repo_root, gate, self.signer, self._role_for(gate),
                               _SIGN_VERDICT[v], feedback)
                except Exception as exc:
                    self.emit({"type": "error", "error": f"Gate {gate} signing failed: {exc}"})
                    return {"status": "sign-failed", "gate": gate, "error": str(exc)}
            self.state.signed.append(gate)
            # Fix 5: record the (unresolved) condition so it blocks readiness.
            if v == "approve-with-conditions":
                self.state.conditions[gate] = str(feedback or "").strip()
            self.emit({"type": "gate_signed", "gate": gate, "verdict": v})
            nxt = self._next_after(gate)
            if nxt is None:
                if self._enforce_outcome_gate:
                    marker = dict(self._release_finalization_marker())
                    marker.update({
                        "status": "pending",
                        "phase": "signed",
                        "run_id": self.state.run_id,
                        "project_id": self.project_id,
                        "profile": self.profile,
                        "updated_at": self._release_timestamp(),
                    })
                    self.state.release_evidence["release_finalization"] = marker
                self.state.status = "complete"
                self._persist()
                finalization: dict = {}
                if self._enforce_outcome_gate:
                    finalization = self._finalize_pending_g5_release()
                else:
                    # Custom signer seams do not own real Git/seal side effects.
                    self._finalize_closeout(ready=True)
                release_truth = self.state.release_evidence.get(
                    "release_verification", {}
                )
                truth_ready = bool(
                    isinstance(release_truth, dict)
                    and release_truth.get("ok") is True
                )
                finalized = (
                    not self._enforce_outcome_gate
                    or finalization.get("status") == "succeeded"
                )
                ready = truth_ready and finalized
                if not ready:
                    reasons = list(
                        release_truth.get("reasons") or []
                        if isinstance(release_truth, dict) else []
                    )
                    if truth_ready and not finalized:
                        last = finalization.get("last_attempt")
                        attempt_status = (
                            str(last.get("status") or "pending")
                            if isinstance(last, dict) else "pending"
                        )
                        reasons.append(
                            f"release finalization is pending ({attempt_status})"
                        )
                    self._release_delivery_lock()
                    terminal_cancel = self.state.status == "cancelled"
                    return {
                        "status": (
                            "cancelled" if terminal_cancel
                            else ("release-not-ready" if not truth_ready
                                  else "release-pending")
                        ),
                        "gate": gate,
                        "ready": False,
                        "reasons": reasons,
                        "release_finalization": finalization,
                    }
                self.emit({
                    "type": "delivery_complete",
                    "run_id": self.state.run_id,
                    "ready": True,
                    "waived": list(self.state.waived),
                    "release_finalization": finalization,
                })
                self._release_delivery_lock()
                return {"status": "complete", "ready": True,
                        "waived": list(self.state.waived),
                        "conditions": dict(self.state.conditions),
                        "release_finalization": finalization}
            # Fix 6: persist the freshly-signed state DURABLY before dispatching
            # the next gate. Previously the next gate ran (mutating state,
            # running the agent) BEFORE any persist, so a crash mid-next-gate
            # left a signature on disk while delivery.json still showed the prior
            # gate awaiting -- an on-disk inconsistency. _persist now also raises
            # on failure, so a failed checkpoint surfaces instead of silently
            # continuing into the next gate.
            self.state.current_gate = nxt
            self.state.status = "active"
            self._persist()
            self._run_gate(nxt)
            return {"status": "advanced", "gate": nxt}

        if v == "request-changes":
            cyc = self.state.rework.get(gate, 0) + 1
            if cyc > self.max_rework:
                # Audit the refused verdict too (mirrors handle_request_changes'
                # max_cycles_reached path) but don't store it as actionable
                # feedback -- the gate will not re-run.
                self._record_review(gate, v, feedback, cyc, store=False)
                self.emit({"type": "error",
                           "error": f"Max rework ({self.max_rework}) reached at {gate}."})
                self._emit_incident("gate-deadlock",
                                    detail=f"'{gate}' hit the rework limit.")
                self.state.status = "stopped"
                self._persist()
                self._release_delivery_lock()
                return {"status": "max-rework", "gate": gate}
            self.state.rework[gate] = cyc
            self._record_review(gate, v, feedback, cyc)
            self._run_gate(gate)
            return {"status": "reworked", "gate": gate, "cycle": cyc}

        if v == "reject":
            cnt = self.state.rejections.get(gate, 0) + 1
            if cnt > self.max_rejections:
                self._record_review(gate, v, feedback, cnt, store=False)
                self.emit({"type": "error",
                           "error": f"Max rejections ({self.max_rejections}) reached at {gate}."})
                self._emit_incident("gate-deadlock",
                                    detail=f"'{gate}' was rejected too many times.")
                self.state.status = "stopped"
                self._persist()
                self._release_delivery_lock()
                return {"status": "max-rejections", "gate": gate}
            self.state.rejections[gate] = cnt
            self._record_review(gate, v, feedback, cnt)
            self._run_gate(gate)
            return {"status": "rejected", "gate": gate, "count": cnt}

        if v == "waive":
            # Fix 5: a waiver is an audited, accountable act -- it advances the
            # gate WITHOUT a signature and marks the delivery not-ready, so it
            # must carry a real reason. A blank waiver is refused.
            if not str(feedback or "").strip():
                self.emit({"type": "error",
                           "error": f"Gate {gate}: a waiver requires a written reason."})
                return {"status": "waive-needs-reason", "gate": gate}
            if gate not in self.state.waived:
                self.state.waived.append(gate)
            self._record_review(gate, v, feedback, 0, store=False)
            self.emit({"type": "system",
                       "text": f"{gate} waived (documented): {feedback or 'no reason given'}"})
            nxt = self._next_after(gate)
            if nxt is None:
                self.state.status = "complete"
                self.state.release_evidence["release_verification"] = {
                    "ok": False,
                    "reasons": ["delivery completed with one or more waived gates"],
                }
                self._persist()
                self.emit({"type": "delivery_complete", "run_id": self.state.run_id,
                           "ready": False, "waived": list(self.state.waived)})
                self._finalize_closeout(ready=False)
                self._release_delivery_lock()
                return {"status": "complete-waived", "ready": False,
                        "waived": list(self.state.waived)}
            self._run_gate(nxt)
            return {"status": "advanced-waived", "gate": nxt}

        self.emit({"type": "error", "error": f"Unknown verdict: {verdict!r}"})
        return {"status": "unknown-verdict"}

    # -- gate reopen (#4) ---------------------------------------------------

    # Delivery statuses in which a reopen is safe: the walk is parked waiting
    # on a human (awaiting-verdict), parked because the agent did not produce
    # reviewable work (blocked, Fix 1), deadlocked (stopped), finished
    # (complete) or already reopened. "active" means a gate agent is mid-run -
    # reopening under it would race the run's own state writes, so it is refused.
    _REOPEN_SAFE_STATUSES = frozenset(
        {"awaiting-verdict", "blocked", "stopped", "complete", "reopened"})

    def _reopen_roles_for(self, gate: str) -> set:
        """Roles authorised to reopen *gate* - the SAME authorisation set
        sign.sign_gate enforces for signing it: the union of required_roles
        across the gate's manifest artifacts (a reopen reverses a signature,
        so it demands the authority that could have produced one)."""
        try:
            from .. import artifacts
            required = {r for a in artifacts.expected_gate_artifacts(gate)
                        for r in a.required_roles}
        except Exception:
            required = set()
        return required or {self._role_for(gate)}

    def _append_reopen_audit(self, entry: dict) -> None:
        """Append one reopen/invalidate row to AUDIT_TRAIL.jsonl.

        Rows that reverse a signature carry an `action` containing a reverse
        marker audit_replay.py recognises ("reopen" / "unsign") plus the plain
        gate id in `gate`, so time-travel replay folds the gate back to
        unsigned. Refusal rows deliberately carry `gate_id` instead of `gate`
        so replay does NOT treat the refused attempt as a reversal.
        Silent on OSError, matching the other audit appenders."""
        audit_path = self.repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
        sign.append_audit_event(
            audit_path,
            {
                **entry,
                # Reopen evidence shares one workspace ledger across virtual
                # projects; keep the project and delivery identity backend-
                # owned so callers cannot redirect or forge either binding.
                "run_id": self.state.run_id,
                "project_id": self.project_id,
            },
        )

    def reopen_gate(self, gate: str, reason: str, name: str = "",
                    role: str = "") -> dict:
        """Reopen an already-signed gate and invalidate everything after it.

        The reopened gate and every LATER signed gate lose their signature
        (recorded in state.invalidated + audited with replay-legible reverse
        markers); later waived gates are un-waived. current_gate returns to
        the reopened gate and the reason is threaded into the gate's feedback
        so the re-run's _gate_message carries it. Bounded per gate by the
        reopen budget (SIGNALOS_GATE_REOPEN_BUDGET, default 3). Does NOT
        re-run the gate agent - the caller decides when to resume."""
        gate = str(gate or "").strip().upper()
        if gate not in GATE_ORDER:
            self.emit({"type": "error", "error": f"Unknown gate {gate!r}"})
            return {"status": "unknown-gate", "gate": gate}
        if self.state.status not in self._REOPEN_SAFE_STATUSES:
            self.emit({"type": "error",
                       "error": f"Cannot reopen {gate}: delivery is "
                                f"'{self.state.status}' (a gate run is in flight)."})
            return {"status": "delivery-busy", "gate": gate,
                    "delivery_status": self.state.status}
        if gate not in self.state.signed:
            self.emit({"type": "error",
                       "error": f"Cannot reopen {gate}: it is not signed."})
            return {"status": "not-signed", "gate": gate}

        # Actor/role are server-owned delivery identity, never caller claims.
        # Keep the legacy parameters for wire compatibility but deliberately
        # ignore them so a persisted-run reopen cannot self-authorize as QA/PE.
        actor = self.signer
        role = self._role_for(gate)
        if role not in self._reopen_roles_for(gate):
            self.emit({"type": "error",
                       "error": f"Role {role!r} is not authorised to reopen "
                                f"{gate} (required: "
                                f"{sorted(self._reopen_roles_for(gate))})."})
            return {"status": "role-not-authorized", "gate": gate, "role": role}

        cnt = self.state.reopens.get(gate, 0) + 1
        if cnt > self.max_reopens:
            # Audited refusal (no `gate` key -> replay ignores it), no state change.
            self._append_reopen_audit({
                "action": "gate-reopen-refused", "kind": "reopen-refused",
                "gate_id": gate, "actor": actor, "role": role,
                "reason": reason, "attempt": cnt, "budget": self.max_reopens,
            })
            self.emit({"type": "error",
                       "error": f"Max reopens ({self.max_reopens}) reached at {gate}."})
            return {"status": "max-reopens", "gate": gate}

        # Cascade: the target gate plus every later signed gate lose their
        # signature; later waived gates lose their waiver. All audited.
        idx = GATE_ORDER.index(gate)
        existing_lock = self._read_delivery_lock()
        owned_before = bool(
            existing_lock
            and str(existing_lock.get("run_id") or "") == self.state.run_id
            and str(existing_lock.get("pid") or "") == str(os.getpid())
            and str(existing_lock.get("host") or "") == _hostname()
            and str(existing_lock.get("owner_token") or "")
            == self._delivery_lock_owner_token
        )
        blocked = self._acquire_delivery_lock()
        if blocked is not None:
            self.emit({"type": "delivery_blocked", "gate": gate,
                       "reason": blocked.get("reason", "delivery lock unavailable"),
                       "active_run_id": blocked.get("active_run_id")})
            return {"status": "blocked", "gate": gate,
                    "reason": blocked.get("reason", "delivery lock unavailable")}
        signed_to_revoke = [
            candidate for candidate in GATE_ORDER[idx:]
            if candidate in self.state.signed
        ]
        try:
            sign.revoke_gates(
                self.repo_root,
                signed_to_revoke,
                project_id=self.project_id,
                reason=reason,
                actor=actor,
            )
        except (OSError, ValueError) as exc:
            if not owned_before:
                self._release_delivery_lock()
            message = f"Cannot reopen {gate}: durable revocation failed ({exc})."
            self.emit({"type": "error", "error": message})
            return {"status": "revocation-failed", "gate": gate,
                    "reason": str(exc)}

        invalidated: list[str] = []
        for g in GATE_ORDER[idx:]:
            cascade = g != gate
            if g in self.state.signed:
                self.state.signed = [s for s in self.state.signed if s != g]
                self.state.invalidated.append(
                    {"gate": g, "by": actor, "reason": reason, "cascade": cascade})
                self._append_reopen_audit({
                    # "reopen"/"unsign" are audit_replay reverse markers.
                    "action": "gate.reopen" if not cascade else "gate.unsign",
                    "kind": "reopen" if not cascade else "invalidate",
                    "gate": g, "actor": actor, "role": role, "reason": reason,
                    "cascade": cascade, "source_gate": gate,
                })
                if cascade:
                    invalidated.append(g)
            elif cascade and g in self.state.waived:
                self.state.waived = [w for w in self.state.waived if w != g]
                self.state.invalidated.append(
                    {"gate": g, "by": actor, "reason": reason,
                     "cascade": True, "waived": True})
                self._append_reopen_audit({
                    "action": "gate.unwaive", "kind": "unwaive",
                    "gate": g, "actor": actor, "role": role, "reason": reason,
                    "cascade": True, "source_gate": gate,
                })
                invalidated.append(g)

        self.state.reopens[gate] = cnt
        # A reopen creates a new release cycle. Never let a prior successful or
        # pending G5 marker suppress finalization of the reworked bytes.
        self.state.release_evidence.pop("release_finalization", None)
        self.state.release_evidence.pop("release_verification", None)
        if idx <= GATE_ORDER.index("G4"):
            self.state.release_evidence.pop("g4_verify", None)
            self.state.release_evidence.pop("security_gate", None)
            self.state.release_evidence.pop("runtime_proof", None)
            self._g4_verify = None
            self._last_runtime_ok = False
        # Thread the reason through the same feedback mechanism rework uses,
        # so _gate_message includes it when the gate re-runs. Also writes the
        # gate_review audit event (verdict REOPEN).
        self._record_review(gate, "reopen", reason, cnt)
        self.state.current_gate = gate
        self.state.status = "reopened"
        self._persist()
        self.emit({"type": "gate_reopened", "gate": gate,
                   "invalidated": invalidated, "reason": reason,
                   "by": actor, "role": role, "reopen_count": cnt})
        # Reopening is immediately actionable: emit the same checkpoint/card
        # contract as a normal gate pause instead of requiring a second manual
        # resume just to recover the verdict controls.
        self.emit({
            "type": "gate",
            "gate": gate,
            "title": f"{GATE_SPECIALISTS[gate]} - {gate}",
            "question": GATE_QUESTIONS[gate],
            "specialist": GATE_SPECIALISTS[gate],
            "reopened": True,
        })
        self.emit({"type": "system",
                   "text": f"{gate} reopened by {actor} ({role}): "
                           f"{reason or 'no reason given'}."
                           + (f" Also invalidated: {', '.join(invalidated)}."
                              if invalidated else "")})
        return {"status": "reopened", "gate": gate, "invalidated": invalidated}

    # -- production-profile post-build release-safety stages ----------------

    def _run_post_build_stages(self) -> Optional[dict]:
        """Run the profile's POST-BUILD release-safety stages, in order.

        Config-gated by the engine profile (PROFILE_STAGES). The benchmark
        profile enables NONE of them, so this is a STRICT no-op that returns
        None -- the benchmark G4 sign is byte-identical to today. Every stage
        runs AFTER the G4 build is independently verified, so it can never
        change the verified-build outcome, the scores, or the product bytes.

        Returns a blocking status dict when a production stage refuses the G4
        sign (a CRITICAL security finding); otherwise None.  Degraded/missing
        evidence is still enforced by the fail-closed G5 readiness decision.
        """
        stages = PROFILE_STAGES[self.profile]
        if stages.get("security_gate"):
            blocked = self._run_security_gate_stage()
            if blocked is not None:
                return blocked
        if stages.get("runtime_proof"):
            self._run_proof_stage()
        return None

    def _run_security_gate_stage(self) -> Optional[dict]:
        """PRODUCTION-ONLY. Call the SINGLE security_gate implementation
        (gitleaks/semgrep-style injection scan) over the built product; a
        CRITICAL finding HARD-BLOCKS the G4 sign. Deliberately OFF in the
        benchmark profile: the grader already scores security, so re-scoring
        here would only add noise. Fail policy: a real finding fails CLOSED
        (blocks the sign), but the gate merely ERRORING ('warning') or an
        unexpected exception fails OPEN (never blocks) -- a flaky scanner must
        not abort the build checkpoint on its own.  It records warning evidence
        that fails the later production G5 readiness check. Never raises."""
        try:
            from .security_gate import run_security_gate, write_security_result
            from .stacks import detect_profile
            stack = detect_profile(self.repo_root)
            result = run_security_gate(
                repo_root=self.repo_root,
                intent=self._load_intent(),
                generated_files=self._product_files_for_scan(),
                profile=stack,
            )
            try:
                write_security_result(result, self.repo_root / ".signalos")
            except OSError:
                pass
            issues = (result.get("injection_scan") or {}).get("issues_found") or []
            status = str(result.get("status") or "")
            self.state.release_evidence["security_gate"] = {
                "status": status,
                "issue_count": len(issues),
            }
            self.emit({"type": "security_gate", "gate": "G4",
                       "status": status, "issue_count": len(issues)})
            # Only a real 'failed' verdict (critical findings) blocks G4. A
            # degraded warning is persisted and later fails G5 readiness.
            if status == "failed":
                reason = (
                    f"Security gate found {len(issues)} critical finding(s) in "
                    "the built product. Fix them before signing the build:\n"
                    + "\n".join(
                        f"- {i.get('file', '?')}:{i.get('line', '?')} "
                        f"{i.get('risk') or i.get('pattern') or 'issue'}"
                        for i in issues[:25]
                    )
                )
                self.emit({"type": "error",
                           "error": f"Gate G4 cannot be signed: {reason}"})
                return {"status": "security-blocked", "gate": "G4",
                        "reason": reason, "issue_count": len(issues)}
        except Exception as exc:  # keep G4 open; G5 rejects warning evidence
            self.state.release_evidence["security_gate"] = {
                "status": "warning",
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.emit({"type": "system",
                       "text": f"Security gate skipped ({type(exc).__name__}: {exc})."})
        return None

    def _run_proof_stage(self) -> None:
        """PRODUCTION-ONLY. Real runtime + UX proof (starts a live dev server /
        headless page) via the SINGLE proof implementation. Deliberately OFF in
        the benchmark profile: real servers/ports/timing are flaky and the
        benchmark's behavioral acceptance tests already own 'the app runs', so
        running it there would inject exactly the variance the benchmark must
        avoid. The stage does not retroactively change G4, but its persisted
        evidence is mandatory at production G5, so unavailable/flaky proof
        infrastructure honestly blocks release readiness. Never raises."""
        # Unknown production stacks fail closed as browser-required until
        # deterministic stack detection proves they are API-only.
        ux_required = True
        stack = "unknown"
        try:
            from .proof import (
                requires_browser_ux_proof,
                run_runtime_proof,
                run_ux_proof,
                write_proof_artifacts,
            )
            from .stacks import detect_profile
            stack = detect_profile(self.repo_root)
            runtime = run_runtime_proof(self.repo_root, stack)
            passed = runtime.get("status") == "passed"
            ux_required = requires_browser_ux_proof(self.repo_root, stack)
            if ux_required:
                html = runtime.get("html_snapshot") if passed else None
                ux = run_ux_proof(
                    self.repo_root, stack,
                    port=runtime.get("port") if passed else None,
                    html=html if isinstance(html, str) and html else None,
                    browser_result=(
                        runtime.get("browser_ux")
                        if isinstance(runtime.get("browser_ux"), dict)
                        else None
                    ),
                )
            else:
                ux = run_ux_proof(self.repo_root, stack, port=None)
            try:
                write_proof_artifacts(runtime, ux, self.repo_root)
            except OSError:
                pass
            # Fix 4: remember the runtime proof outcome so the G5 release
            # verifier can refuse to report a PRODUCTION delivery ready-to-ship
            # without a passing runtime proof (evidence-only, never a hard
            # block at G4).
            ux_executed = ux.get("executed") is True
            ux_passed = ux.get("status") == "passed"
            proof_ok = passed and (not ux_required or (ux_passed and ux_executed))
            self._last_runtime_ok = proof_ok
            self.state.release_evidence["runtime_proof"] = {
                "status": runtime.get("status"),
                "stack": stack,
                "ux_required": ux_required,
                "ux_status": ux.get("status"),
                "ux_executed": ux_executed,
                "ux_schema_version": ux.get("schema_version"),
                "ok": proof_ok,
            }
            self.emit({"type": "proof", "gate": "G4",
                       "runtime_status": runtime.get("status"),
                       "ux_status": ux.get("status")})
        except Exception as exc:  # evidence-only -- a hiccup never fails the walk
            self._last_runtime_ok = False
            self.state.release_evidence["runtime_proof"] = {
                "status": "error",
                "stack": stack,
                "ux_required": ux_required,
                "ux_status": "unmeasurable" if ux_required else "skipped",
                "ux_executed": False,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.emit({"type": "system",
                       "text": f"Runtime proof skipped ({type(exc).__name__}: {exc})."})

    def _load_intent(self) -> dict:
        """Best-effort product intent for the security gate: the delivery's
        persisted INTENT.json when present, else a minimal intent from the
        prompt. The security gate tolerates an empty/minimal intent."""
        try:
            from .intent import load_intent
            data = load_intent(self.repo_root / ".signalos")
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"product_name": self.repo_root.name, "prompt": self.state.prompt}

    def _product_files_for_scan(self) -> list:
        """Real product source files (repo-relative) the security gate scans:
        the stack's own source dir, code suffixes only. Mirrors what
        run_delivery hands run_security_gate, gathered from disk here."""
        src = self.repo_root / self._product_source_dir()
        out: list[str] = []
        if src.is_dir():
            for p in sorted(src.rglob("*")):
                if p.is_file() and p.suffix in self._CODE_SUFFIXES:
                    out.append(str(p.relative_to(self.repo_root)).replace("\\", "/"))
        return out

    # -- G5 release verification (Fix 4 + Fix 5) ----------------------------

    def _unresolved_conditions(self) -> list:
        """(gate, text) for every approve-with-conditions still unresolved.
        There is no resolve API yet, so any recorded condition is unresolved and
        blocks readiness -- an "approve with conditions" cannot silently ship."""
        return [(g, t) for g, t in self.state.conditions.items()]

    def _prior_gate_release_reasons(self) -> list[str]:
        """Fail-closed G5 prerequisites for every prior gate.

        Both durable delivery state membership and canonical on-disk strict
        validation are required. State alone can be stale/corrupt; files alone
        can belong to a different walk. The strict validator covers presence,
        current hashes, approved verdicts, audit linkage, authorized roles, and
        durable revocation.
        """
        reasons: list[str] = []
        for gate in GATE_ORDER[:-1]:
            if gate not in self.state.signed:
                reasons.append(f"{gate}: missing from this delivery's signed state")
            try:
                strict = sign.check_gate_signed_strict(
                    self.repo_root, gate, project_id=self.project_id)
            except Exception as exc:
                reasons.append(
                    f"{gate}: canonical strict signature validation errored "
                    f"({type(exc).__name__}: {exc})"
                )
                continue
            if not strict.signed:
                details = list(strict.reasons or [])
                if details:
                    reasons.extend(str(detail) for detail in details)
                else:
                    reasons.append(f"{gate}: canonical strict signature validation failed")
        return reasons

    def _verify_g5_release(
        self, *, pending_conditions: Optional[dict[str, str]] = None
    ) -> dict:
        """Fix 4: a DETERMINISTIC release verifier that decides delivery
        READINESS at G5. Readiness must reflect real verification, never merely
        the absence of waivers. Returns ``{"ok": bool, "reasons": [str]}``.

        Not-ready (G5 remains unsigned and open for remediation) when ANY of:
          * a gate was WAIVED (advanced without a signature); or
          * an approve-with-conditions is UNRESOLVED (Fix 5); or
          * there is no built product to ship (no real product source on disk
            -- never report ready-to-ship over an empty/stub repo); or
          * in the PRODUCTION profile, security evidence is not exactly passed;
            or the runtime proof did not pass.

        """
        reasons: list[str] = []
        release_digest = ""
        if self.state.waived:
            reasons.append("waived gate(s): " + ", ".join(map(str, self.state.waived)))
        unresolved = self._unresolved_conditions()
        if pending_conditions:
            unresolved.extend((g, t) for g, t in pending_conditions.items())
        if unresolved:
            reasons.append("unresolved condition(s) on gate(s): "
                           + ", ".join(g for g, _ in unresolved))
        try:
            has_product = self._repo_has_real_product_src()
        except Exception:
            has_product = False
        if not has_product:
            reasons.append("no built product source on disk to ship")
        # A real production G5 walk must prove every prior gate twice: membership
        # in this delivery state plus canonical strict on-disk signatures. The
        # explicit custom-sign test seam owns its own gate truth. Direct
        # readiness probes at another gate remain useful and do not claim a G5
        # release decision.
        real_g5_walk = self._enforce_outcome_gate and self.state.current_gate == "G5"
        if real_g5_walk:
            reasons.extend(self._prior_gate_release_reasons())
            # Unconditional: corrupted state cannot omit G4 to skip tree proof.
            g4 = self._g4_verification_for_current_tree()
            if not g4.get("ok"):
                reasons.append("G4 product proof is not current: "
                               + str(g4.get("reason") or "verification missing"))
            else:
                attribution = g4.get("attribution")
                if isinstance(attribution, dict):
                    release_digest = str(attribution.get("release_digest") or "")
        elif "G4" in self.state.signed:
            # Non-production/direct seam: still bind when the caller claims G4,
            # without imposing production's canonical-signature contract on a
            # custom test signer.
            g4 = self._g4_verification_for_current_tree()
            if not g4.get("ok"):
                reasons.append("G4 product proof is not current: "
                               + str(g4.get("reason") or "verification missing"))
            else:
                attribution = g4.get("attribution")
                if isinstance(attribution, dict):
                    release_digest = str(attribution.get("release_digest") or "")
        if real_g5_walk and not release_digest:
            reasons.append("G4 proof does not expose a bound release-tree digest")
        if self.profile == "production":
            security = self.state.release_evidence.get("security_gate")
            if (
                not isinstance(security, dict)
                or str(security.get("status") or "") != "passed"
            ):
                reasons.append("production profile: security gate did not pass")
            runtime = self.state.release_evidence.get("runtime_proof")
            if (
                not isinstance(runtime, dict)
                or runtime.get("status") != "passed"
                or runtime.get("ok") is not True
                or not getattr(self, "_last_runtime_ok", False)
            ):
                reasons.append("production profile: runtime proof did not pass")
            persisted_ux_required = (
                runtime.get("ux_required") if isinstance(runtime, dict) else None
            )
            if type(persisted_ux_required) is not bool:
                reasons.append(
                    "production profile: explicit UX requirement evidence is missing"
                )
                persisted_ux_required = True
            current_ux_required = True
            try:
                from .proof import requires_browser_ux_proof
                from .stacks import detect_profile

                current_stack = detect_profile(self.repo_root)
                current_ux_required = requires_browser_ux_proof(
                    self.repo_root, current_stack,
                )
                if (
                    isinstance(runtime, dict)
                    and str(runtime.get("stack") or "") != current_stack
                ):
                    reasons.append(
                        "production profile: runtime proof stack no longer matches the product"
                    )
                if persisted_ux_required is not current_ux_required:
                    reasons.append(
                        "production profile: persisted UX requirement no longer matches the product"
                    )
            except Exception as exc:
                reasons.append(
                    "production profile: browser UX requirement could not be determined "
                    f"({type(exc).__name__}: {exc})"
                )
            ux_required = bool(persisted_ux_required or current_ux_required)
            if ux_required:
                ux_status = runtime.get("ux_status") if isinstance(runtime, dict) else None
                ux_executed = (
                    runtime.get("ux_executed") if isinstance(runtime, dict) else None
                )
                ux_schema = (
                    runtime.get("ux_schema_version")
                    if isinstance(runtime, dict) else None
                )
                if ux_status != "passed":
                    reasons.append(
                        "production profile: browser UX proof did not pass"
                    )
                if ux_executed is not True:
                    reasons.append(
                        "production profile: browser UX proof was not executed"
                    )
                if ux_schema != "signalos.ux-browser-proof.v1":
                    reasons.append(
                        "production profile: browser UX proof schema is missing or invalid"
                    )
        return {
            "ok": not reasons,
            "reasons": reasons,
            "release_digest": release_digest,
            "checked_at": self._release_timestamp(),
        }

    @staticmethod
    def _release_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _cancel_at_boundary(self, boundary: str) -> Optional[dict]:
        """Let an observed cancel win before an irreversible operation."""
        try:
            requested = bool(self.cancel_check())
        except Exception:
            requested = False
        if not requested:
            return None
        self.state.status = "cancelled"
        self.state.last_outcome = {
            "gate": self.state.current_gate,
            "ok": False,
            "reason": f"cancellation observed before {boundary}",
            "loop_status": "cancelled",
        }
        self._persist()
        self.emit({
            "type": "cancelled",
            "run_id": self.state.run_id,
            "gate": self.state.current_gate,
            "reason": self.state.last_outcome["reason"],
        })
        self._release_delivery_lock()
        return {
            "run_id": self.state.run_id,
            "gate": self.state.current_gate,
            "status": "cancelled",
            "reason": self.state.last_outcome["reason"],
        }

    def _release_digest_from_g4(self) -> str:
        """The exact shippable-tree digest bound by current G4 evidence."""
        proof = self._g4_verification_for_current_tree()
        attribution = proof.get("attribution") if isinstance(proof, dict) else None
        if not isinstance(attribution, dict):
            return ""
        return str(attribution.get("release_digest") or "")

    def _verify_completed_g5_release(self) -> dict:
        """Recompute release truth after G5 signing and on every complete resume.

        Persisted ``release_verification.ok`` is deliberately ignored.  The
        current bytes, conditions/runtime policy, delivery-state membership,
        and canonical strict signatures for *all* G0-G5 gates must still agree.
        """
        checked = self._verify_g5_release()
        reasons = list(checked.get("reasons") or [])
        if self.state.current_gate != "G5":
            reasons.append(
                f"terminal delivery gate is {self.state.current_gate!r}, expected 'G5'"
            )
        for gate in GATE_ORDER:
            if gate not in self.state.signed:
                reasons.append(f"{gate}: missing from this delivery's signed state")
            try:
                strict = sign.check_gate_signed_strict(
                    self.repo_root, gate, project_id=self.project_id)
            except Exception as exc:
                reasons.append(
                    f"{gate}: canonical strict signature validation errored "
                    f"({type(exc).__name__}: {exc})"
                )
                continue
            if not strict.signed:
                details = list(strict.reasons or [])
                reasons.extend(
                    (str(detail) for detail in details)
                    if details
                    else [f"{gate}: canonical strict signature validation failed"]
                )
        # Preserve order while collapsing repeated reasons from the pre-sign
        # verifier and the unconditional all-gates completion check.
        reasons = list(dict.fromkeys(str(reason) for reason in reasons if reason))
        digest = self._release_digest_from_g4()
        if not digest:
            reasons.append("G4 proof does not expose a bound release-tree digest")
        return {
            "ok": not reasons,
            "reasons": reasons,
            "release_digest": digest,
            "checked_at": self._release_timestamp(),
        }

    def _release_finalization_marker(self) -> dict:
        marker = self.state.release_evidence.get("release_finalization")
        return marker if isinstance(marker, dict) else {}

    def _release_marker_reasons(
        self, marker: dict, *, release_digest: str = ""
    ) -> list[str]:
        """Validate that a finalization receipt belongs to this exact walk."""
        reasons: list[str] = []
        if marker.get("schema_version") != "signalos.release-finalization.v1":
            reasons.append("release finalization marker schema is missing or invalid")
        if str(marker.get("run_id") or "") != self.state.run_id:
            reasons.append("release finalization marker run_id mismatch")
        if str(marker.get("project_id") or "") != self.project_id:
            reasons.append("release finalization marker project_id mismatch")
        if str(marker.get("profile") or "") != self.profile:
            reasons.append("release finalization marker profile mismatch")
        status = str(marker.get("status") or "")
        if status not in {"pending", "succeeded"}:
            reasons.append(f"release finalization marker status {status!r} is invalid")
        phase = str(marker.get("phase") or "")
        if phase not in ({"signed"} if status == "succeeded" else {"verified", "signed"}):
            reasons.append(
                f"release finalization marker phase {phase!r} is invalid for {status!r}"
            )
        bound = str(marker.get("release_digest") or "")
        if not bound:
            reasons.append("release finalization marker has no release-tree digest")
        if release_digest and bound != release_digest:
            reasons.append("release finalization marker release-tree digest mismatch")
        if status == "succeeded":
            outcome = marker.get("outcome")
            if not isinstance(outcome, dict):
                reasons.append("successful release marker has no finalizer outcome")
            else:
                reasons.extend(self._release_success_reasons(outcome, bound))
        return reasons

    def _release_success_reasons(
        self, outcome: dict, release_digest: str,
    ) -> list[str]:
        """Validate the durable local and remote receipts for a shipped release."""
        reasons: list[str] = []
        if str(outcome.get("status") or "") != "succeeded":
            reasons.append("release finalizer outcome is not succeeded")

        seal = outcome.get("seal")
        if not isinstance(seal, dict) or seal.get("status") != "ok":
            reasons.append("successful release has no valid integrity-seal outcome")
        else:
            if str(seal.get("project_id") or "") != self.project_id:
                reasons.append("integrity seal project_id mismatch")
            wave = str(seal.get("wave") or "")
            seal_rel = str(seal.get("path") or "")
            seal_sha = str(seal.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", seal_sha):
                reasons.append("integrity seal receipt has no valid sha256")
            try:
                from ..commands.seal import (
                    compute_seal,
                    seal_path as canonical_seal_path,
                    verify_seal,
                )

                expected_path = canonical_seal_path(
                    self.repo_root, wave, project_id=self.project_id,
                )
                expected_rel = expected_path.relative_to(
                    self.repo_root.resolve()
                ).as_posix()
                if seal_rel != expected_rel:
                    reasons.append(
                        "integrity seal receipt path is not the exact canonical "
                        "wave/project path"
                    )
                # Read only the canonical path.  A receipt-controlled path is
                # never an alternate authority source, even inside .signalos.
                actual = hashlib.sha256(expected_path.read_bytes()).hexdigest()
                if seal_sha and actual != seal_sha:
                    reasons.append("integrity seal receipt hash mismatch")
                canonical = compute_seal(
                    self.repo_root, project_id=self.project_id,
                )
                expected_count = len(canonical)
                verification = verify_seal(
                    self.repo_root, wave, project_id=self.project_id,
                )
                if verification.get("status") != "ok":
                    details = list(verification.get("errors") or [])
                    mismatch_count = len(verification.get("mismatches") or [])
                    suffix = (
                        ": " + "; ".join(map(str, details[:4]))
                        if details else (
                            f" ({mismatch_count} artifact mismatch(es))"
                            if mismatch_count else ""
                        )
                    )
                    reasons.append(
                        "integrity seal semantic verification failed" + suffix
                    )
                if verification.get("checked") != expected_count:
                    reasons.append(
                        "integrity seal did not verify the complete canonical artifact set"
                    )
                if seal.get("total") != expected_count:
                    reasons.append(
                        "integrity seal receipt total does not match the canonical artifact set"
                    )
                expected_sealed = sum(1 for entry in canonical if entry.get("exists"))
                if seal.get("sealed") != expected_sealed:
                    reasons.append(
                        "integrity seal receipt sealed count does not match current artifacts"
                    )
            except (OSError, ValueError, ReleaseTreeError) as exc:
                reasons.append(f"integrity seal receipt is unreadable or unsafe ({exc})")

        commit = outcome.get("commit")
        commit_sha = ""
        if not isinstance(commit, dict) or commit.get("status") not in {
            "committed", "already-committed",
        }:
            reasons.append("successful release has no committed Git outcome")
        else:
            commit_sha = str(commit.get("sha") or "").lower()
            if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha):
                reasons.append("release commit receipt has no valid object id")
            else:
                receipt = sign._release_commit_at_head(
                    self.repo_root,
                    f"{self.project_id}:{self.state.run_id}",
                    release_digest,
                    self.project_id,
                )
                if receipt != commit_sha:
                    reasons.append("release commit receipt no longer matches exact HEAD bytes")

        push = outcome.get("push")
        if not isinstance(push, dict) or push.get("status") != "ok":
            reasons.append("successful release has no successful push outcome")
        else:
            if push.get("verified") is not True:
                reasons.append("remote push was not read back and verified")
            if str(push.get("remote") or "") != "origin":
                reasons.append("remote push receipt is not bound to origin")
            ref = str(push.get("ref") or "")
            if not ref.startswith("refs/heads/") or ref == "refs/heads/":
                reasons.append("remote push receipt has no exact branch ref")
            if str(push.get("sha") or "").lower() != commit_sha:
                reasons.append("remote push receipt commit does not match release commit")
            remote_hash = str(push.get("remote_url_sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", remote_hash):
                reasons.append("remote push receipt has no bound remote URL digest")
            elif (
                commit_sha
                and ref.startswith("refs/heads/")
                and ref != "refs/heads/"
            ):
                # A persisted receipt is only a claim about a past push.  On
                # every completed resume, independently re-read the configured
                # origin and its exact branch ref so changing origin or moving
                # the remote branch invalidates readiness.  Never include Git
                # stdout/stderr here: either may contain credential-bearing
                # remote URLs.
                try:
                    configured = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=str(self.repo_root),
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                except (OSError, subprocess.SubprocessError):
                    configured = None
                remote_url = (
                    (configured.stdout or "").strip()
                    if configured is not None and configured.returncode == 0
                    else ""
                )
                if not remote_url:
                    reasons.append("origin remote URL could not be verified")
                elif (
                    hashlib.sha256(remote_url.encode("utf-8")).hexdigest()
                    != remote_hash
                ):
                    reasons.append("origin remote URL no longer matches release receipt")

                try:
                    remote = subprocess.run(
                        ["git", "ls-remote", "--exit-code", "origin", ref],
                        cwd=str(self.repo_root),
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                except (OSError, subprocess.SubprocessError):
                    remote = None
                remote_rows: list[str] = []
                if remote is not None and remote.returncode == 0:
                    for line in (remote.stdout or "").splitlines():
                        fields = line.split()
                        if len(fields) == 2 and fields[1] == ref:
                            remote_rows.append(fields[0].lower())
                if remote_rows != [commit_sha]:
                    reasons.append("origin remote ref no longer matches release commit")
        return list(dict.fromkeys(reasons))

    def _set_release_verification(self, result: dict) -> None:
        self.state.release_evidence["release_verification"] = {
            "ok": bool(result.get("ok")),
            "reasons": list(result.get("reasons") or []),
            "release_digest": str(result.get("release_digest") or ""),
            "checked_at": str(result.get("checked_at") or self._release_timestamp()),
        }

    def _g5_finalization_lock_path(self) -> Path:
        # Git index/HEAD/origin and integrity seals are workspace-global even
        # though delivery locks are project-scoped. Serialize finalizers across
        # every virtual project in this worktree.
        return safe_control_path(
            self.repo_root, ".signalos", "locks", "g5-release.lock",
        )

    def _try_acquire_g5_finalization_lock(self) -> Optional[str]:
        path = self._g5_finalization_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        for attempt in range(2):
            tmp = path.parent / f".g5-release-{os.getpid()}-{uuid.uuid4().hex}.tmp"
            try:
                with tmp.open("x", encoding="utf-8") as handle:
                    json.dump({
                        "token": token,
                        "run_id": self.state.run_id,
                        "project_id": self.project_id,
                        "pid": os.getpid(),
                        "host": _hostname(),
                        "acquired_at": self._release_timestamp(),
                    }, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(tmp, path)
                    return token
                except FileExistsError:
                    pass
            except FileExistsError:
                pass
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                owner = {}
            if str(owner.get("host") or "") == _hostname():
                live = _pid_is_alive(owner.get("pid"))
            else:
                acquired = _parse_iso_utc(owner.get("acquired_at"))
                age = ((datetime.now(timezone.utc) - acquired).total_seconds()
                       if acquired is not None else _LOCK_TTL_SECONDS + 1)
                live = age < _LOCK_TTL_SECONDS
            if attempt == 0 and not live:
                try:
                    path.unlink()
                except OSError:
                    return None
                continue
            return None
        return None

    def _release_g5_finalization_lock(self, token: Optional[str]) -> None:
        if not token:
            return
        path = self._g5_finalization_lock_path()
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
            if owner.get("token") == token:
                path.unlink()
        except (OSError, ValueError, TypeError):
            return

    def _finalize_pending_g5_release(self) -> dict:
        """Advance one durable, idempotent G5 finalization attempt."""
        marker = self._release_finalization_marker()
        if marker.get("status") != "pending":
            return dict(marker)
        cancelled = self._cancel_at_boundary("G5 finalization")
        if cancelled is not None:
            marker = dict(marker)
            marker["last_attempt"] = {
                "status": "cancelled",
                "outcome": cancelled,
                "at": self._release_timestamp(),
            }
            self.state.release_evidence["release_finalization"] = marker
            self._persist()
            return marker
        token = self._try_acquire_g5_finalization_lock()
        if token is None:
            pending = dict(marker)
            pending["reason"] = "another workspace release finalizer is active"
            return pending
        try:
            truth = self._verify_completed_g5_release()
            marker_errors = self._release_marker_reasons(
                marker, release_digest=str(truth.get("release_digest") or ""),
            )
            if marker_errors:
                truth = dict(truth)
                truth["ok"] = False
                truth["reasons"] = list(dict.fromkeys(
                    list(truth.get("reasons") or []) + marker_errors
                ))
            self._set_release_verification(truth)
            marker = dict(marker)
            marker["attempts"] = int(marker.get("attempts") or 0) + 1
            marker["verification"] = {
                "ok": bool(truth.get("ok")),
                "reasons": list(truth.get("reasons") or []),
                "release_digest": str(truth.get("release_digest") or ""),
                "checked_at": truth.get("checked_at"),
            }
            marker["updated_at"] = self._release_timestamp()
            self.state.release_evidence["release_finalization"] = marker
            # This pending checkpoint precedes every seal/commit/push side
            # effect. If it cannot be made durable, nothing is released.
            self._persist()
            if not truth.get("ok"):
                marker["reason"] = "current release truth failed before finalization"
                marker["status"] = "pending"
                marker["last_attempt"] = {
                    "status": "failed",
                    "reasons": list(truth.get("reasons") or []),
                    "at": self._release_timestamp(),
                }
                marker["updated_at"] = self._release_timestamp()
                self.state.release_evidence["release_finalization"] = marker
                self.state.status = "blocked"
                self.state.last_outcome = {
                    "gate": "G5",
                    "ok": False,
                    "reason": marker["reason"],
                    "loop_status": "release-verification-failed",
                }
                self._persist()
                self.emit({
                    "type": "release_finalization_failed",
                    "gate": "G5",
                    "reasons": list(truth.get("reasons") or []),
                })
                return dict(marker)

            # Closeout is part of the release commit and therefore must exist
            # before the finalizer stages/commits/pushes the tree. It is safe to
            # regenerate on a pending resume.
            self._finalize_closeout(ready=True)
            cancelled = self._cancel_at_boundary("release seal")
            if cancelled is not None:
                marker["last_attempt"] = {
                    "status": "cancelled",
                    "outcome": cancelled,
                    "at": self._release_timestamp(),
                }
                self.state.release_evidence["release_finalization"] = marker
                self._persist()
                return dict(marker)
            try:
                outcome = sign.finalize_g5_release(
                    self.repo_root,
                    release_id=f"{self.project_id}:{self.state.run_id}",
                    release_digest=str(truth.get("release_digest") or ""),
                    project_id=self.project_id,
                    cancel_check=self.cancel_check,
                )
            except Exception as exc:
                outcome = {
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            if not isinstance(outcome, dict):
                outcome = {"status": "failed", "reason": "finalizer returned no outcome"}
            status = str(outcome.get("status") or "failed")
            if status not in {"succeeded", "deferred", "failed", "cancelled"}:
                outcome = dict(outcome)
                outcome["reason"] = f"unknown finalizer status {status!r}"
                status = "failed"
            if status == "succeeded":
                receipt_errors = self._release_success_reasons(
                    outcome, str(truth.get("release_digest") or ""),
                )
                if receipt_errors:
                    outcome = dict(outcome)
                    outcome.update({
                        "status": "failed",
                        "reason": "release finalizer returned an invalid success receipt",
                        "receipt_errors": receipt_errors,
                    })
                    status = "failed"
            marker["status"] = "succeeded" if status == "succeeded" else "pending"
            marker["last_attempt"] = {
                "status": status,
                "outcome": outcome,
                "at": self._release_timestamp(),
            }
            if status == "succeeded":
                marker["outcome"] = outcome
            elif status == "cancelled":
                self.state.status = "cancelled"
            marker["updated_at"] = self._release_timestamp()
            self.state.release_evidence["release_finalization"] = marker
            self._persist()
            event = {
                "succeeded": "release_finalized",
                "deferred": "release_finalization_deferred",
                "failed": "release_finalization_failed",
                "cancelled": "cancelled",
            }[status]
            self.emit({"type": event, "gate": "G5", "outcome": outcome})
            return dict(marker)
        finally:
            self._release_g5_finalization_lock(token)

    def _recover_g5_release(self) -> dict:
        """Crash recovery used only by an explicit delivery resume.

        A pending/verified marker plus a canonical strict G5 signature proves a
        crash occurred after signing but before the terminal checkpoint; in that
        one case state is reconciled instead of signing twice. Complete resumes
        always recompute current truth, even when finalization already succeeded.
        """
        marker = self._release_finalization_marker()
        eligible_signed_crash = (
            marker.get("status") == "pending"
            and marker.get("phase") in {"verified", "signed"}
            and self.state.status in {"active", "awaiting-verdict"}
        )
        if eligible_signed_crash:
            reasons: list[str] = []
            if self.state.current_gate != "G5":
                reasons.append("pending release is not positioned at G5")
            try:
                strict_g5 = sign.check_gate_signed_strict(
                    self.repo_root, "G5", project_id=self.project_id,
                    require_release_proof=False,
                )
            except Exception as exc:
                strict_g5 = None
                reasons.append(
                    "G5 canonical strict signature validation errored "
                    f"({type(exc).__name__}: {exc})"
                )
            if strict_g5 is None or not strict_g5.signed:
                if strict_g5 is not None:
                    reasons.extend(str(reason) for reason in strict_g5.reasons or [])
                # No complete G5 signature means there is nothing to reconcile;
                # leave the gate open for a normal, freshly verified approval.
                return {**marker, "recovery_reasons": reasons}
            current = self._verify_g5_release()
            reasons.extend(str(reason) for reason in current.get("reasons") or [])
            expected_digest = str(marker.get("release_digest") or "")
            current_digest = str(current.get("release_digest") or "")
            reasons.extend(self._release_marker_reasons(
                marker, release_digest=current_digest,
            ))
            if not expected_digest or current_digest != expected_digest:
                reasons.append("pending release digest no longer matches current G4 proof")
            if reasons:
                marker = dict(marker)
                marker.update({
                    "status": "failed",
                    "reason": "post-sign crash recovery validation failed",
                    "recovery_reasons": list(dict.fromkeys(reasons)),
                    "updated_at": self._release_timestamp(),
                })
                self.state.release_evidence["release_finalization"] = marker
                self._set_release_verification({
                    "ok": False,
                    "reasons": marker["recovery_reasons"],
                    "release_digest": current_digest,
                    "checked_at": self._release_timestamp(),
                })
                self._persist()
                return marker
            if "G5" not in self.state.signed:
                self.state.signed.append("G5")
            self.state.status = "complete"
            marker = dict(marker)
            marker.update({
                "phase": "signed",
                "updated_at": self._release_timestamp(),
            })
            self.state.release_evidence["release_finalization"] = marker
            self._persist()

        if self.state.status == "complete":
            # Unconditional recomputation closes the cached-ok resume bypass.
            truth = self._verify_completed_g5_release()
            marker = self._release_finalization_marker()
            marker_errors = (
                self._release_marker_reasons(
                    marker,
                    release_digest=str(truth.get("release_digest") or ""),
                )
                if marker else []
            )
            if marker_errors:
                truth = dict(truth)
                truth["ok"] = False
                truth["reasons"] = list(dict.fromkeys(
                    list(truth.get("reasons") or []) + marker_errors
                ))
            self._set_release_verification(truth)
            self._persist()
            if marker.get("status") == "pending" and not marker_errors:
                return self._finalize_pending_g5_release()
            return {
                **marker,
                "verification": self.state.release_evidence.get(
                    "release_verification", {}
                ),
            }
        return dict(self._release_finalization_marker())

    def _finalize_closeout(self, *, ready: bool) -> None:
        """CONVERGENCE (Claim 2): produce the delivery CLOSEOUT via the SAME
        closeout.build_closeout / write_closeout the full run_delivery pipeline
        uses, so a fix to the closeout service reaches BOTH engines instead of
        only the CLI path. Previously a completed GateOrchestrator walk (the
        desktop `agent:deliver` surface) emitted `delivery_complete` but wrote
        NO CLOSEOUT.json, while run_delivery did -- the exact divergence the
        audit flags.

        Behaviour-preserving for the benchmark's G4 build:
          * Runs ONLY at final completion, strictly AFTER G4 is signed and
            independently verified, so it cannot change the G4 build behaviour,
            the scores, or the verified-build outcome for an already-scaffolded
            React repo.
          * Writes ONLY .signalos governance evidence (CLOSEOUT.json/.md);
            never touches product source or the G4 build outputs.
          * Deliberately does NOT invoke write_handoff_files here: its GTM stage
            is LLM-gated and would add a provider call to the walk. Handoff/GTM
            convergence is left for a design decision (see the workstream note).
          * Opt-out via finalize_closeout=False (a strict no-op), and never
            raises -- a closeout hiccup is reported through emit and swallowed,
            so it can never fail the walk or perturb a driver that does not want
            completion writes.
        """
        if not self.finalize_closeout:
            return
        try:
            from .closeout import build_closeout, write_closeout
            from .stacks import detect_profile
            profile = detect_profile(self.repo_root)
            closeout = build_closeout(self.repo_root, self.repo_root.name,
                                      profile, None)
            write_closeout(closeout, self.repo_root / ".signalos")
            self.emit({"type": "closeout",
                       "closure_level": closeout.get("closure_level"),
                       "ready": ready})
        except Exception as exc:  # never fail the walk on a closeout hiccup
            self.emit({"type": "system",
                       "text": f"Closeout skipped ({type(exc).__name__}: {exc})."})

    def _state_dir(self) -> Path:
        return agent_run_dir(self.repo_root, self.state.run_id)

    def _persist(self) -> None:
        # Fix 6: persistence is a DURABILITY checkpoint the walk depends on
        # (apply_verdict persists the freshly-signed state before dispatching the
        # next gate). A failed write must SURFACE, not be silently swallowed --
        # continuing past a failed checkpoint is exactly how an on-disk signature
        # and delivery.json drift apart. Callers that must not fail the walk on a
        # persist hiccup handle it explicitly; the checkpoint path does not.
        d = self._state_dir()
        d.mkdir(parents=True, exist_ok=True)
        target = d / "delivery.json"
        tmp = d / f".delivery-{os.getpid()}-{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(asdict(self.state), indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def resume_delivery(
    repo_root: Path,
    run_id: str,
    adapter: Any,
    emit: Callable[[dict], None],
    *,
    enforcement_provider: Optional[EnforcementProvider] = None,
    sign_fn: Optional[Callable[..., list]] = None,
    signer: str = "foundry-agent",
    profile: str | None = None,
    legacy_profile: str = DEFAULT_PROFILE,
    recover_pending_release: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> "GateOrchestrator":
    """Reconstruct a GateOrchestrator from its persisted delivery.json (INV-5).

    Used after a sidecar crash/restart to resume from the last checkpoint.
    Raises FileNotFoundError if no state was persisted.  The persisted profile
    is authoritative.  An optional *profile* is an expected value and must
    match it; *legacy_profile* is used only for pre-profile state files.
    ``recover_pending_release`` is reserved for the explicit agent-resume
    surface: it recomputes complete-run truth and retries only a durable pending
    G5 finalization. Read-only/one-shot reconstruction never pushes implicitly.
    """
    root = Path(repo_root)
    requested_run_id = validate_run_id(run_id)
    run_dir = agent_run_dir(root, requested_run_id)
    state_file = safe_control_path(
        root, ".signalos", "agent-runs", requested_run_id, "delivery.json",
    )
    if state_file.parent != run_dir:
        raise ValueError("persisted delivery state is not bound to its run directory")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("persisted delivery state must be a JSON object")
    if str(data.get("run_id") or "") != requested_run_id:
        raise ValueError("persisted delivery run_id does not match the requested run_id")
    has_persisted_profile = "profile" in data
    persisted_raw = data.get("profile")
    if not has_persisted_profile:
        persisted_profile = validate_orchestrator_profile(
            profile if profile is not None else legacy_profile
        )
    else:
        persisted_profile = validate_orchestrator_profile(str(persisted_raw))
        if profile is not None:
            expected_profile = validate_orchestrator_profile(profile)
            if expected_profile != persisted_profile:
                raise ValueError(
                    "resume profile mismatch: persisted "
                    f"{persisted_profile!r}, requested {expected_profile!r}"
                )
    persisted_signer = str(data.get("signer") or signer).strip() or "foundry-agent"
    orch = GateOrchestrator(
        repo_root, adapter, emit,
        enforcement_provider=enforcement_provider, sign_fn=sign_fn,
        signer=persisted_signer, run_id=run_id, prompt=data.get("prompt", ""),
        # §3.2: restore the persisted project binding so a resumed delivery
        # keeps signing/generating in the same namespace it started in.
        # Older persisted states predate the field -> "default".
        project_id=str(data.get("project_id") or "default"),
        profile=persisted_profile,
        cancel_check=cancel_check,
    )
    st = orch.state
    st.current_gate = data.get("current_gate", "G0")
    st.status = data.get("status", "active")
    if st.status == "blocked":
        orch._enforce_blocked_checkpoint = True
    st.rework = dict(data.get("rework", {}))
    st.rejections = dict(data.get("rejections", {}))
    st.signed = list(data.get("signed", []))
    st.waived = list(data.get("waived", []))
    # Older persisted states predate the feedback field -- tolerate absence.
    st.feedback = dict(data.get("feedback", {}))
    # Reopen bookkeeping (#4) - also absent from older persisted states.
    st.reopens = dict(data.get("reopens", {}))
    st.invalidated = list(data.get("invalidated", []))
    # Fix 5 conditions + Fix 1 last_outcome - absent from older persisted states.
    st.conditions = dict(data.get("conditions", {}))
    st.last_outcome = dict(data.get("last_outcome", {}))
    st.profile = persisted_profile
    st.signer = persisted_signer
    st.release_evidence = dict(data.get("release_evidence", {}))
    g4_verify = st.release_evidence.get("g4_verify")
    orch._g4_verify = dict(g4_verify) if isinstance(g4_verify, dict) else None
    runtime = st.release_evidence.get("runtime_proof")
    orch._last_runtime_ok = bool(
        isinstance(runtime, dict)
        and runtime.get("ok") is True
        and runtime.get("status") == "passed"
        and (
            runtime.get("ux_required") is False
            or (
                runtime.get("ux_required") is True
                and runtime.get("ux_status") == "passed"
                and runtime.get("ux_executed") is True
                and runtime.get("ux_schema_version")
                == "signalos.ux-browser-proof.v1"
            )
        )
    )
    if not has_persisted_profile or not str(data.get("signer") or "").strip():
        # One-time fail-closed migration for legacy checkpoints.  If this write
        # cannot be made, resume surfaces the failure instead of running with an
        # identity/profile that would be forgotten again on the next restart.
        orch._persist()
    terminal = st.status in {"complete", "stopped", "cancelled"}
    if not terminal or recover_pending_release:
        blocked = orch._acquire_delivery_lock()
        if blocked is not None:
            raise RuntimeError(str(blocked.get("reason") or "delivery lock is held"))
    if recover_pending_release:
        try:
            orch._recover_g5_release()
        except Exception:
            # A failed reconstruction is never registered as the lock owner.
            orch._release_delivery_lock()
            raise
        if orch.state.status in {"complete", "stopped", "cancelled"}:
            orch._release_delivery_lock()
    return orch


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
