"""Gate orchestration (Phase 5 / T26-T44) - the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a bounded
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Bounded rework (3) / reject (2); waive
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


def _default_sign(repo_root: Path, gate: str, signer: str, role: str,
                  verdict: str, conditions: str) -> list:
    """Production signing path - INV-3: sign.py is the ONLY signer.

    Writes the audit trail (T38) and co-signs gates whose artifacts require
    different roles (e.g. G3) by signing each artifact with an authorised role."""
    from .. import artifacts
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    wave = _current_wave_id(repo_root)
    present = [a for a in artifacts.resolve_gate_artifacts(repo_root, gate)
               if a.path.is_file()]
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
        max_rework: int = 3,
        max_rejections: int = 2,
        run_id: Optional[str] = None,
        prompt: str = "",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.adapter = adapter
        self.emit = emit
        self.enforcement_provider = enforcement_provider
        self.signer = signer
        self._sign = sign_fn or _default_sign
        self.project_id = project_id
        self.max_rework = max_rework
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
        if gate == self.state.current_gate and self.state.rework.get(gate):
            return base + f"\n\n[Rework cycle {self.state.rework[gate]} - address the prior feedback.]"
        return base

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
        self.state.status = "awaiting-verdict"
        self._persist()
        return result

    def _emit_preview(self, gate: str) -> None:
        try:
            from .design_preview import generate_design_preview_html
            html = generate_design_preview_html({}, {"prompt": self.state.prompt})
        except Exception:
            html = "<!DOCTYPE html><html><body><main>Design preview</main></body></html>"
        self.emit({"type": "preview", "srcDoc": html, "caption": f"{gate} design preview"})

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
                self.emit({"type": "error",
                           "error": f"Max rework ({self.max_rework}) reached at {gate}."})
                self.state.status = "stopped"
                self._persist()
                return {"status": "max-rework", "gate": gate}
            self.state.rework[gate] = cyc
            self._run_gate(gate)
            return {"status": "reworked", "gate": gate, "cycle": cyc}

        if v == "reject":
            cnt = self.state.rejections.get(gate, 0) + 1
            if cnt > self.max_rejections:
                self.emit({"type": "error",
                           "error": f"Max rejections ({self.max_rejections}) reached at {gate}."})
                self.state.status = "stopped"
                self._persist()
                return {"status": "max-rejections", "gate": gate}
            self.state.rejections[gate] = cnt
            self._run_gate(gate)
            return {"status": "rejected", "gate": gate, "count": cnt}

        if v == "waive":
            if gate not in self.state.waived:
                self.state.waived.append(gate)
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
    return orch


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
