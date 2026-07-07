"""Gate orchestration (Phase 5 / T26-T44) - the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a budgeted
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Budgeted rework / reject (2); waive
advances without signing and marks the delivery not-"ready" (INV-1).
"""
from __future__ import annotations

import functools
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import agent_loader, wave_engine, sign
from .agent_loop import AgentLoop
from .budgets import resolve_gate_reopen_budget, resolve_gate_rework_budget
from .enforcement_state import EnforcementProvider

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
                  project_id: str = "default") -> list:
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
    # Fail-closed (0.1): a gate that declares required artifacts cannot be
    # signed until at least one of them exists on disk. Previously an empty
    # `present` signed nothing, raised nothing, and the gate advanced anyway
    # -- a silent fail-open where a founder could approve a gate whose agent
    # produced no artifact. Gates with no declared artifacts are unaffected.
    if expected and not present:
        raise ValueError(
            f"gate {gate} cannot be signed: none of its {len(expected)} "
            "required artifact(s) exist yet ("
            + ", ".join(a.rel_path for a in expected) + ")"
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
                              project_id=project_id)
    signed = []
    for a in present:
        r = role if role in a.required_roles else a.required_roles[0]
        sign.sign_artifact(a.path, signer, r, gate, verdict, conditions)
        sign._append_audit(audit_log, signer, r, gate, a.rel_path, a.path, verdict, wave=wave)
        signed.append(a.rel_path)
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
    ) -> None:
        self.repo_root = Path(repo_root)
        self.adapter = adapter
        # 1.3 + 1.8: an optional second adapter used to author the plain-words
        # gate brief. When configured (and its vendor differs), the brief is
        # genuinely cross-vendor (1.4's independence guarantee). When absent,
        # falls back to the primary adapter -- still a real 4-field brief,
        # honestly recorded as same-vendor in its provenance.
        self.critic_adapter = critic_adapter
        self.emit = emit
        self.enforcement_provider = enforcement_provider
        self.signer = signer
        self.project_id = project_id
        # §3.2: bind the delivery's project namespace into the default sign
        # path so signatures land under the SAME governance dir inspect()
        # reads. Custom sign_fn callables (tests, IPC overrides) keep their
        # historical 6-arg signature untouched.
        if sign_fn is not None:
            self._sign = sign_fn
        else:
            self._sign = functools.partial(_default_sign, project_id=project_id)
        self.max_rework = resolve_gate_rework_budget(max_rework)
        self.max_rejections = max_rejections
        self.max_reopens = resolve_gate_reopen_budget(max_reopens)
        # run_id must be unique per delivery - it keys the persisted state dir
        # (.signalos/agent-runs/<run_id>/delivery.json). An explicit run_id is
        # honored verbatim (resume path); the fallback adds a timestamp + uuid
        # suffix so two deliveries with the same prompt prefix never collide.
        rid = run_id or (
            f"delivery-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        )
        self.state = DeliveryState(run_id=rid, prompt=prompt,
                                   project_id=project_id,
                                   current_gate=self._current_gate())

    def _current_gate(self) -> str:
        try:
            insp = wave_engine.inspect(self.repo_root, self.project_id)
            return insp.get("next_gate") or "G0"
        except Exception:
            return "G0"

    def _next_after(self, gate: str) -> Optional[str]:
        idx = GATE_ORDER.index(gate)
        return GATE_ORDER[idx + 1] if idx + 1 < len(GATE_ORDER) else None

    def _role_for(self, gate: str) -> str:
        return GATE_ROLES.get(gate, "PO")

    def _gate_message(self, gate: str) -> str:
        base = self.state.prompt or "Proceed with the delivery."
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
        loop = AgentLoop(
            adapter=self.adapter,
            repo_root=self.repo_root,
            enforcement_provider=self.enforcement_provider,
            emit=self.emit,
            execution_context="delivery",
            active_gate=gate,
            # §3.2 creation side: the loop rebases gate-artifact writes
            # (core/governance|strategy|execution/**) under this project's
            # governance base, so the artifact this gate generates is the
            # one _default_sign/inspect/status resolve at sign time.
            project_id=self.project_id,
            signed_gates=[
                int(str(g).lstrip("G"))
                for g in self.state.signed
                if str(g).lstrip("G").isdigit()
            ],
        )
        result = loop.run(system_prompt, self._gate_message(gate))
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
        self.state.status = "awaiting-verdict"
        self._persist()
        return result

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

    def start(self) -> dict:
        gate = self.state.current_gate
        self._run_gate(gate)
        return {"run_id": self.state.run_id, "gate": gate, "status": self.state.status}

    def apply_verdict(self, verdict: str, feedback: str = "") -> dict:
        gate = self.state.current_gate
        v = verdict if verdict in _KNOWN_VERDICTS else _classify(verdict)

        if v in ("approve", "approve-with-conditions"):
            try:
                self._sign(self.repo_root, gate, self.signer, self._role_for(gate),
                           _SIGN_VERDICT[v], feedback)
            except Exception as exc:
                self.emit({"type": "error", "error": f"Gate {gate} signing failed: {exc}"})
                return {"status": "sign-failed", "gate": gate, "error": str(exc)}
            self.state.signed.append(gate)
            self.emit({"type": "gate_signed", "gate": gate, "verdict": v})
            nxt = self._next_after(gate)
            if nxt is None:
                self.state.status = "complete"
                self._persist()
                ready = len(self.state.waived) == 0
                self.emit({"type": "delivery_complete", "run_id": self.state.run_id,
                           "ready": ready, "waived": list(self.state.waived)})
                return {"status": "complete", "ready": ready, "waived": list(self.state.waived)}
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
                return {"status": "max-rejections", "gate": gate}
            self.state.rejections[gate] = cnt
            self._record_review(gate, v, feedback, cnt)
            self._run_gate(gate)
            return {"status": "rejected", "gate": gate, "count": cnt}

        if v == "waive":
            if gate not in self.state.waived:
                self.state.waived.append(gate)
            self._record_review(gate, v, feedback, 0, store=False)
            self.emit({"type": "system",
                       "text": f"{gate} waived (documented): {feedback or 'no reason given'}"})
            nxt = self._next_after(gate)
            if nxt is None:
                self.state.status = "complete"
                self._persist()
                self.emit({"type": "delivery_complete", "run_id": self.state.run_id,
                           "ready": False, "waived": list(self.state.waived)})
                return {"status": "complete-waived", "ready": False,
                        "waived": list(self.state.waived)}
            self._run_gate(nxt)
            return {"status": "advanced-waived", "gate": nxt}

        self.emit({"type": "error", "error": f"Unknown verdict: {verdict!r}"})
        return {"status": "unknown-verdict"}

    # -- gate reopen (#4) ---------------------------------------------------

    # Delivery statuses in which a reopen is safe: the walk is parked waiting
    # on a human (awaiting-verdict), deadlocked (stopped), finished (complete)
    # or already reopened. "active" means a gate agent is mid-run - reopening
    # under it would race the run's own state writes, so it is refused.
    _REOPEN_SAFE_STATUSES = frozenset(
        {"awaiting-verdict", "stopped", "complete", "reopened"})

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
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "run_id": self.state.run_id,
                **entry,
            }
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

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

        actor = str(name or "").strip() or self.signer
        role = str(role or "").strip() or self._role_for(gate)
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
        self.emit({"type": "system",
                   "text": f"{gate} reopened by {actor} ({role}): "
                           f"{reason or 'no reason given'}."
                           + (f" Also invalidated: {', '.join(invalidated)}."
                              if invalidated else "")})
        return {"status": "reopened", "gate": gate, "invalidated": invalidated}

    def _state_dir(self) -> Path:
        return self.repo_root / ".signalos" / "agent-runs" / self.state.run_id

    def _persist(self) -> None:
        try:
            d = self._state_dir()
            d.mkdir(parents=True, exist_ok=True)
            (d / "delivery.json").write_text(
                json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        except OSError:
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
) -> "GateOrchestrator":
    """Reconstruct a GateOrchestrator from its persisted delivery.json (INV-5).

    Used after a sidecar crash/restart to resume from the last checkpoint.
    Raises FileNotFoundError if no state was persisted."""
    state_file = Path(repo_root) / ".signalos" / "agent-runs" / run_id / "delivery.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    orch = GateOrchestrator(
        repo_root, adapter, emit,
        enforcement_provider=enforcement_provider, sign_fn=sign_fn,
        signer=signer, run_id=run_id, prompt=data.get("prompt", ""),
        # §3.2: restore the persisted project binding so a resumed delivery
        # keeps signing/generating in the same namespace it started in.
        # Older persisted states predate the field -> "default".
        project_id=str(data.get("project_id") or "default"),
    )
    st = orch.state
    st.current_gate = data.get("current_gate", "G0")
    st.status = data.get("status", "active")
    st.rework = dict(data.get("rework", {}))
    st.rejections = dict(data.get("rejections", {}))
    st.signed = list(data.get("signed", []))
    st.waived = list(data.get("waived", []))
    # Older persisted states predate the feedback field -- tolerate absence.
    st.feedback = dict(data.get("feedback", {}))
    # Reopen bookkeeping (#4) - also absent from older persisted states.
    st.reopens = dict(data.get("reopens", {}))
    st.invalidated = list(data.get("invalidated", []))
    return orch


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
