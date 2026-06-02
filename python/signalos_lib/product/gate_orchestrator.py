"""Gate orchestration (Phase 5 / T26-T44) — the gate-aware supervisor.

Per architecture decision Q4 the AgentLoop is gate-UNAWARE: it runs a bounded
conversation and returns. This module is the supervisor that walks G0->G5:
run the gate agent, pause for review (gate event), and on the user's verdict
sign via sign.py (INV-3) and advance. Bounded rework (3) / reject (2); waive
advances without signing and marks the delivery not-"ready" (INV-1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from .. import agent_loader, wave_engine, sign
from .agent_loop import AgentLoop
from .enforcement_state import EnforcementProvider

__all__ = ["GateOrchestrator", "GATE_SPECIALISTS", "GATE_QUESTIONS", "GATE_ROLES"]

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
# gate (sign_gate rejects a role not authorised for any present artifact),
# derived from artifacts.GATE_MAP: G0 needs PE (not PO), G5 needs QA, etc.
# NOTE: G3 mixes PO (DESIGN_NOTE) and PE (PLAN/ACCEPTANCE) artifacts, so a real
# G3 sign requires co-signing — handled per-artifact in _default_sign.
GATE_ROLES = {
    "G0": "PE", "G1": "PO", "G2": "PO", "G3": "PE", "G4": "PE", "G5": "QA",
}

# gate_review verdict -> sign.py VALID_VERDICTS
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
    status: str = "active"  # active | awaiting-verdict | complete | stopped
    rework: dict = field(default_factory=dict)
    rejections: dict = field(default_factory=dict)
    signed: list = field(default_factory=list)
    waived: list = field(default_factory=list)   # gates waived (INV-1)


def _default_sign(repo_root: Path, gate: str, signer: str, role: str,
                  verdict: str, conditions: str) -> list:
    """Production signing path — INV-3: sign.py is the ONLY signer.

    Writes the audit trail (T38) and co-signs gates whose artifacts require
    different roles (e.g. G3) by signing each artifact with an authorised
    role rather than one gate-wide role."""
    from .. import artifacts
    audit_log = repo_root / ".signalos" / "AUDIT_TRAIL.jsonl"
    present = [a for a in artifacts.resolve_gate_artifacts(repo_root, gate)
               if a.path.is_file()]
    # Fast path: one role covers every present artifact -> single sign_gate.
    if present and all(role in a.required_roles for a in present):
        return sign.sign_gate(repo_root, gate, signer, role, verdict,
                              conditions, audit_log=audit_log)
    # Co-sign path: sign each artifact with a role it authorises (sign.py only).
    signed = []
    for a in present:
        r = role if role in a.required_roles else a.required_roles[0]
        sign.sign_artifact(a.path, signer, r, gate, verdict, conditions)
        sign._append_audit(audit_log, signer, r, gate, a.rel_path, a.path, verdict)
        signed.append(a.rel_path)
    return signed


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
        rid = run_id or ("delivery-" + (prompt[:12] or "run"))
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
            return base + f"\n\n[Rework cycle {self.state.rework[gate]} — address the prior feedback.]"
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
        )
        result = loop.run(system_prompt, self._gate_message(gate))
        if gate == "G3":
            self._emit_preview(gate)
        self.emit({
            "type": "gate",
            "gate": gate,
            "title": f"{GATE_SPECIALISTS[gate]} · {gate}",
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

    def _emit_gate_pause(self, gate: str) -> None:
        self.emit({
            "type": "gate",
            "gate": gate,
            "title": f"{GATE_SPECIALISTS[gate]} - {gate}",
            "question": GATE_QUESTIONS[gate],
            "specialist": GATE_SPECIALISTS[gate],
        })

    def start(self) -> dict:
        gate = self.state.current_gate
        self._run_gate(gate)
        return {"run_id": self.state.run_id, "gate": gate, "status": self.state.status}

    @classmethod
    def load(
        cls,
        repo_root: Path,
        adapter: Any,
        emit: Callable[[dict], None],
        *,
        run_id: str,
        enforcement_provider: Optional[EnforcementProvider] = None,
        signer: str = "foundry-agent",
        sign_fn: Optional[Callable[..., list]] = None,
        project_id: str = "default",
        max_rework: int = 3,
        max_rejections: int = 2,
    ) -> "GateOrchestrator":
        path = Path(repo_root) / ".signalos" / "agent-runs" / run_id / "delivery.json"
        if not path.is_file():
            raise FileNotFoundError(f"no persisted delivery for run {run_id}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"delivery state unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("delivery state must be a JSON object")

        prompt = str(raw.get("prompt") or "")
        orch = cls(
            repo_root,
            adapter,
            emit,
            enforcement_provider=enforcement_provider,
            signer=signer,
            sign_fn=sign_fn,
            project_id=project_id,
            max_rework=max_rework,
            max_rejections=max_rejections,
            run_id=run_id,
            prompt=prompt,
        )
        gate = str(raw.get("current_gate") or orch.state.current_gate or "G0")
        if gate not in GATE_ORDER:
            gate = "G0"
        orch.state = DeliveryState(
            run_id=str(raw.get("run_id") or run_id),
            prompt=prompt,
            current_gate=gate,
            status=str(raw.get("status") or "awaiting-verdict"),
            rework=raw.get("rework") if isinstance(raw.get("rework"), dict) else {},
            rejections=(
                raw.get("rejections") if isinstance(raw.get("rejections"), dict) else {}
            ),
            signed=raw.get("signed") if isinstance(raw.get("signed"), list) else [],
            waived=raw.get("waived") if isinstance(raw.get("waived"), list) else [],
        )
        return orch

    def resume(self) -> dict:
        gate = self.state.current_gate if self.state.current_gate in GATE_ORDER else "G0"
        if self.state.status == "complete":
            ready = len(self.state.waived) == 0
            self.emit({
                "type": "delivery_complete",
                "run_id": self.state.run_id,
                "ready": ready,
                "waived": list(self.state.waived),
            })
            return {
                "run_id": self.state.run_id,
                "status": "complete",
                "ready": ready,
                "waived": list(self.state.waived),
                "resumed": True,
            }
        if self.state.status == "stopped":
            self.emit({
                "type": "error",
                "error": f"Delivery {self.state.run_id} was stopped at {gate}.",
            })
            return {
                "run_id": self.state.run_id,
                "gate": gate,
                "status": "stopped",
                "resumed": True,
            }
        self.state.current_gate = gate
        self.state.status = "awaiting-verdict"
        self._emit_gate_pause(gate)
        self._persist()
        return {
            "run_id": self.state.run_id,
            "gate": gate,
            "status": self.state.status,
            "resumed": True,
        }

    def apply_verdict(self, verdict: str, feedback: str = "") -> dict:
        gate = self.state.current_gate
        v = verdict if verdict in _KNOWN_VERDICTS else _classify(verdict)

        if v in ("approve", "approve-with-conditions"):
            try:
                self._sign(self.repo_root, gate, self.signer, self._role_for(gate),
                           _SIGN_VERDICT[v], feedback)
            except Exception as exc:  # INV-4: surface, do not swallow.
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
            # INV-1: a waive advances but can NEVER satisfy a mandatory proof,
            # so the delivery can no longer close as "ready".
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


def _classify(text: str) -> str:
    from .gate_review import classify_review
    return classify_review(text).get("verdict", "request-changes")
