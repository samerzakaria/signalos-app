"""Gate orchestration (Phase 5 / T26-T44) - the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a budgeted
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Budgeted rework / reject (2); waive
advances without signing and marks the delivery not-"ready" (INV-1).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import agent_loader, wave_engine, sign
from .agent_loop import AgentLoop
from .budgets import resolve_gate_rework_budget
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
    rework: dict = field(default_factory=dict)
    rejections: dict = field(default_factory=dict)
    signed: list = field(default_factory=list)
    waived: list = field(default_factory=list)
    # Reviewer feedback per gate, in verdict order. Each entry is
    # {"verdict": "request-changes"|"reject", "cycle": int, "feedback": str}.
    # Persisted with the rest of the state so a resumed delivery still knows
    # what the reviewer actually asked for. Older persisted states lack this
    # field; resume_delivery tolerates its absence (defaults to {}).
    feedback: dict = field(default_factory=dict)


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
                  verdict: str, conditions: str) -> list:
    """Production signing path - INV-3: sign.py is the ONLY signer.

    Writes the audit trail (T38) and co-signs gates whose artifacts require
    different roles (e.g. G3) by signing each artifact with an authorised role."""
    from .. import artifacts
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    wave = _current_wave_id(repo_root)
    expected = artifacts.resolve_gate_artifacts(repo_root, gate)
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
                              conditions, audit_log=audit_log, wave=wave)
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


def _current_wave_id(repo_root: Path) -> str | None:
    try:
        from ..status import get_wave_status

        wave = str(get_wave_status(repo_root).get("wave_id") or "").strip()
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
        self._sign = sign_fn or _default_sign
        self.project_id = project_id
        self.max_rework = resolve_gate_rework_budget(max_rework)
        self.max_rejections = max_rejections
        # run_id must be unique per delivery - it keys the persisted state dir
        # (.signalos/agent-runs/<run_id>/delivery.json). An explicit run_id is
        # honored verbatim (resume path); the fallback adds a timestamp + uuid
        # suffix so two deliveries with the same prompt prefix never collide.
        rid = run_id or (
            f"delivery-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        )
        self.state = DeliveryState(run_id=rid, prompt=prompt, current_gate=self._current_gate())

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
        if not cyc and not rej:
            return base
        entries = [e for e in self.state.feedback.get(gate, [])
                   if str(e.get("feedback") or "").strip()]
        if not entries:
            # Legacy persisted state (pre-feedback field) or an empty feedback
            # string: keep the old generic nudge rather than fabricating text.
            return base + f"\n\n[Rework cycle {cyc or rej} - address the prior feedback.]"
        latest = entries[-1]
        if latest.get("verdict") == "reject":
            header = (f"[Rejection {rej} - the reviewer rejected the previous "
                      "output; regenerate it, addressing this feedback:]")
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

            resolved = [a for a in artifacts_mod.resolve_gate_artifacts(self.repo_root, gate)
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
            for a in artifacts.resolve_gate_artifacts(self.repo_root, gate):
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
    return orch


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
