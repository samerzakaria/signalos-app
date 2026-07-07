"""Gate orchestration tests (T26-T38): the G0->G5 walk, verdict handling,
sign-on-approve (INV-3), bounded rework/reject, G3 preview, persistence.

Deterministic: an end-turn adapter (no provider/network) + a recording
sign_fn double (so INV-3's sign.py call is asserted without needing real
gate artifacts on disk).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import (
    GateOrchestrator,
    GATE_SPECIALISTS,
    resume_delivery,
)


class _EndAdapter:
    """Adapter stub: every turn ends immediately (no tools)."""
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(content="(gate work done)", tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


class _BriefCapableAdapter:
    """1.3 + 1.8: serves both the main gate-agent loop (tools passed -> ends
    the turn) and, via _CriticChat, the brief-authoring call (no tools ->
    returns a valid 4-field brief as JSON)."""
    supports_tool_calls = True

    def __init__(self, model: str, brief_json: str):
        self.model = model
        self._brief_json = brief_json

    def chat(self, messages, model=None, tools=None, stream=False):
        if tools:
            return AgentResponse(content="(gate work done)", tool_calls=None,
                                 stop_reason="end_turn", usage=TokenUsage())
        return AgentResponse(content=self._brief_json, tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


_GOOD_BRIEF_JSON = (
    '{"what_you_are_signing": "the core purpose", '
    '"what_changes_after": "the plan commits", '
    '"the_one_risk": "scope too broad", '
    '"question_worth_asking": "is this the key outcome?"}'
)


def _orch(root, events, signed, *, max_rework=None):
    def fake_sign(repo_root, gate, signer, role, verdict, conditions):
        signed.append((gate, role, verdict))
        return [f"{gate}.md"]
    return GateOrchestrator(
        Path(root), _EndAdapter(), events.append,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=fake_sign, prompt="build task management",
        max_rework=max_rework,
    )


class TestRealBriefWiring(unittest.TestCase):
    """1.3 + 1.8: the real 4-field critic brief is emitted at every gate,
    routed through model_router.route(), not the flat single-field brief."""

    def _seed_soul_doc(self, root: Path) -> None:
        soul = root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
        soul.parent.mkdir(parents=True, exist_ok=True)
        soul.write_text("The product purpose statement. " * 40, encoding="utf-8")
        (root / ".signalos").mkdir(parents=True, exist_ok=True)

    def test_no_critic_configured_falls_back_honestly(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._seed_soul_doc(root)
            events: list[dict] = []
            adapter = _BriefCapableAdapter("anthropic/claude-sonnet-4-5", _GOOD_BRIEF_JSON)
            orch = GateOrchestrator(
                root, adapter, events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: ["x"], prompt="build",
            )
            orch.start()
            briefs = [e for e in events if e.get("type") == "brief"]
            self.assertTrue(briefs, "no brief event emitted")
            b = briefs[0]
            self.assertEqual(b["the_one_risk"], "scope too broad")  # real 4-field content
            # honest self-report: same adapter authored + reviewed -> not independent
            self.assertTrue(b["contract_violations"])

    def test_cross_vendor_critic_produces_an_independent_brief(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._seed_soul_doc(root)
            events: list[dict] = []
            author = _BriefCapableAdapter("anthropic/claude-sonnet-4-5", "unused")
            critic = _BriefCapableAdapter("openai/gpt-4o", _GOOD_BRIEF_JSON)
            orch = GateOrchestrator(
                root, author, events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: ["x"], prompt="build",
                critic_adapter=critic,
            )
            orch.start()
            briefs = [e for e in events if e.get("type") == "brief"]
            self.assertTrue(briefs, "no brief event emitted")
            b = briefs[0]
            self.assertEqual(b["provenance"]["reviewer_agent"], "Critic")
            self.assertEqual(b["provenance"]["reviewer_model"], "openai/gpt-4o")
            self.assertEqual(b["contract_violations"], [])  # real cross-vendor independence


class TestGateWalk(unittest.TestCase):
    def test_start_pauses_at_g0(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            res = orch.start()
            self.assertEqual(res["gate"], "G0")
            gates = [e for e in events if e.get("type") == "gate"]
            self.assertEqual(len(gates), 1)
            self.assertEqual(gates[0]["gate"], "G0")
            self.assertEqual(gates[0]["specialist"], GATE_SPECIALISTS["G0"])
            # state persisted (INV-5)
            sf = Path(d) / ".signalos" / "agent-runs" / orch.state.run_id / "delivery.json"
            self.assertTrue(sf.is_file())
            self.assertEqual(json.loads(sf.read_text())["current_gate"], "G0")

    def test_approve_signs_and_advances(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            events.clear()
            res = orch.apply_verdict("approve")
            self.assertEqual(res, {"status": "advanced", "gate": "G1"})
            self.assertIn(("G0", "PE", "APPROVED"), signed)   # INV-3 sign path called
            self.assertTrue(any(e.get("type") == "gate_signed" and e["gate"] == "G0" for e in events))
            self.assertTrue(any(e.get("type") == "gate" and e["gate"] == "G1" for e in events))

    def test_conditions_signs_with_conditions_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            orch.apply_verdict("approve-with-conditions", "ship after smoke test")
            self.assertIn(("G0", "PE", "APPROVED-WITH-CONDITIONS"), signed)

    def test_request_changes_reworks_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed, max_rework=3)
            orch.start()
            r1 = orch.apply_verdict("request-changes", "tighten scope")
            self.assertEqual(r1["status"], "reworked")
            self.assertEqual(r1["cycle"], 1)
            # exceed explicit max_rework=3: cycles 2,3 ok, 4th stops
            orch.apply_verdict("request-changes", "again")
            orch.apply_verdict("request-changes", "again")
            r4 = orch.apply_verdict("request-changes", "again")
            self.assertEqual(r4["status"], "max-rework")
            self.assertEqual(orch.state.current_gate, "G0")  # never advanced

    def test_reject_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            self.assertEqual(orch.apply_verdict("reject")["status"], "rejected")
            self.assertEqual(orch.apply_verdict("reject")["status"], "rejected")
            self.assertEqual(orch.apply_verdict("reject")["status"], "max-rejections")
            # 1.10: the deadlock surfaces as a plain-words incident card, not just
            # a bare error.
            incidents = [e for e in events if e.get("type") == "incident"]
            self.assertTrue(incidents)
            self.assertTrue(incidents[-1]["recovery_options"])

    def test_g3_emits_preview(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()                       # G0
            orch.apply_verdict("approve")      # -> G1
            orch.apply_verdict("approve")      # -> G2
            events.clear()
            orch.apply_verdict("approve")      # -> G3 (preview + gate)
            self.assertTrue(any(e.get("type") == "preview" for e in events))
            self.assertTrue(any(e.get("type") == "gate" and e["gate"] == "G3" for e in events))

    def test_gate_emits_completeness_advisory(self):
        """1.9: a substantial gate artifact that addresses no standard concerns
        surfaces an advisory completeness signal (never blocks)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            soul = root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
            soul.parent.mkdir(parents=True, exist_ok=True)
            soul.write_text("The product purpose statement. " * 40, encoding="utf-8")
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            events = []
            orch = GateOrchestrator(
                root, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: ["x"], prompt="build",
            )
            orch.start()  # runs the G0 gate -> completeness advisory over SOUL
            self.assertTrue(any(e.get("type") == "completeness" for e in events))

    def test_g3_emits_ux_friction(self):
        """0.7: the previously dormant UX-friction QA now runs on the design
        preview and is surfaced to the founder at the G3 design gate."""
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()                       # G0
            orch.apply_verdict("approve")      # -> G1
            orch.apply_verdict("approve")      # -> G2
            events.clear()
            orch.apply_verdict("approve")      # -> G3 (preview + ux-friction)
            self.assertTrue(any(e.get("type") == "ux_friction" for e in events))

    def test_full_walk_to_complete(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            for _ in range(5):                 # G0->...->G5
                orch.apply_verdict("approve")
            res = orch.apply_verdict("approve")  # sign G5 -> complete
            self.assertEqual(res["status"], "complete")
            self.assertTrue(any(e.get("type") == "delivery_complete" for e in events))
            self.assertEqual([g for (g, _r, _v) in signed],
                             ["G0", "G1", "G2", "G3", "G4", "G5"])

    def test_waive_advances_without_sign(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            res = orch.apply_verdict("waive", "not applicable to MVP")
            self.assertEqual(res["status"], "advanced-waived")
            self.assertEqual(res["gate"], "G1")
            self.assertNotIn("G0", [g for (g, _r, _v) in signed])  # INV-1: no sign

    def test_resume_delivery_reconstructs_current_gate(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            events.clear()

            loaded = resume_delivery(
                Path(d),
                orch.state.run_id,
                _EndAdapter(),
                events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: [],
            )

            self.assertEqual(loaded.state.run_id, orch.state.run_id)
            self.assertEqual(loaded.state.current_gate, "G0")
            self.assertEqual(loaded.state.status, "awaiting-verdict")


if __name__ == "__main__":
    unittest.main()


class TestDeliveryIPC(unittest.TestCase):
    """Wiring test: agent:deliver starts the walk and emits a gate event;
    agent:verdict advances it. Uses the IPC injection seams + stdout capture."""

    def test_deliver_then_approve_advances(self):
        import io, contextlib, os, json as _json
        import signalos_ipc_server as srv

        signed = []
        srv._AGENT_ADAPTER_FACTORY = lambda model: _EndAdapter()
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(trust_tier="T3")
        srv._DELIVERY_SIGN_FN = lambda root, gate, signer, role, verdict, conditions: signed.append((gate, verdict)) or [f"{gate}.md"]
        try:
            with tempfile.TemporaryDirectory() as d:
                cwd = os.getcwd()
                os.chdir(d)
                try:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r1 = srv.handle({"command": "agent:deliver",
                                         "args": [_json.dumps({"prompt": "build task mgmt", "run_id": "del-1", "provider": "openai", "model": "gpt-test"})],
                                         "id": "1"})
                    self.assertTrue(r1["ok"], r1)
                    self.assertEqual(r1["data"]["gate"], "G0")
                    events = [_json.loads(l) for l in buf.getvalue().splitlines() if l.strip().startswith("{")]
                    self.assertTrue(any(e.get("type") == "gate" and e.get("gate") == "G0" for e in events))

                    buf2 = io.StringIO()
                    with contextlib.redirect_stdout(buf2):
                        r2 = srv.handle({"command": "agent:verdict",
                                         "args": [_json.dumps({"run_id": "del-1", "verdict": "approve"})],
                                         "id": "2"})
                    self.assertTrue(r2["ok"], r2)
                    self.assertEqual(r2["data"]["status"], "advanced")
                    self.assertEqual(r2["data"]["gate"], "G1")
                    self.assertIn(("G0", "APPROVED"), signed)
                    ev2 = [_json.loads(l) for l in buf2.getvalue().splitlines() if l.strip().startswith("{")]
                    self.assertTrue(any(e.get("type") == "gate_signed" and e.get("gate") == "G0" for e in ev2))
                    self.assertTrue(any(e.get("type") == "gate" and e.get("gate") == "G1" for e in ev2))
                finally:
                    os.chdir(cwd)
        finally:
            srv._AGENT_ADAPTER_FACTORY = None
            srv._AGENT_ENFORCEMENT_FACTORY = None
            srv._DELIVERY_SIGN_FN = None
            srv._ACTIVE_DELIVERIES.clear()

    def test_resume_delivery_after_sidecar_memory_loss(self):
        import io, contextlib, os, json as _json
        import signalos_ipc_server as srv

        srv._AGENT_ADAPTER_FACTORY = lambda model: _EndAdapter()
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(trust_tier="T3")
        srv._DELIVERY_SIGN_FN = lambda root, gate, signer, role, verdict, conditions: [f"{gate}.md"]
        try:
            with tempfile.TemporaryDirectory() as d:
                cwd = os.getcwd()
                os.chdir(d)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        r1 = srv.handle({"command": "agent:deliver",
                                         "args": [_json.dumps({"prompt": "build CRM", "run_id": "del-resume", "provider": "openai", "model": "gpt-test"})],
                                         "id": "1"})
                    self.assertTrue(r1["ok"], r1)
                    srv._ACTIVE_DELIVERIES.clear()

                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r2 = srv.handle({"command": "agent:resume",
                                         "args": [_json.dumps({"run_id": "del-resume", "provider": "openai", "model": "gpt-test"})],
                                         "id": "2"})
                    self.assertTrue(r2["ok"], r2)
                    self.assertTrue(r2["data"]["resumed"])
                    self.assertEqual(r2["data"]["gate"], "G0")
                    events = [_json.loads(l) for l in buf.getvalue().splitlines() if l.strip().startswith("{")]
                    self.assertTrue(any(e.get("type") == "gate" and e.get("gate") == "G0" for e in events))
                    self.assertIn("del-resume", srv._ACTIVE_DELIVERIES)
                finally:
                    os.chdir(cwd)
        finally:
            srv._AGENT_ADAPTER_FACTORY = None
            srv._AGENT_ENFORCEMENT_FACTORY = None
            srv._DELIVERY_SIGN_FN = None
            srv._ACTIVE_DELIVERIES.clear()


class TestRealSignAuditAndWaive(unittest.TestCase):
    """T38: the REAL sign.py path writes the audit trail (no fake signer).
    T37: a waived gate makes the delivery close not-'ready' (INV-1)."""

    def test_real_sign_writes_audit_trail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Seed a real G0 artifact (SOUL-DOCUMENT, signable by PE).
            soul = root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
            soul.parent.mkdir(parents=True, exist_ok=True)
            soul.write_text("# Soul Document\n\nThe product purpose.\n", encoding="utf-8")
            signalos_dir = root / ".signalos"
            signalos_dir.mkdir(parents=True, exist_ok=True)
            (signalos_dir / "worktree-state.json").write_text(
                json.dumps({"wave_id": "W7"}) + "\n",
                encoding="utf-8",
            )
            events = []
            # NOTE: no sign_fn -> uses the real _default_sign / sign.py path.
            orch = GateOrchestrator(
                root, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                prompt="build it",
            )
            orch.start()
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "advanced")
            # artifact now carries a signature block
            self.assertIn("Signatures", soul.read_text(encoding="utf-8"))
            # audit trail row written (T38)
            audit = root / ".signalos" / "AUDIT_TRAIL.jsonl"
            self.assertTrue(audit.is_file(), "AUDIT_TRAIL.jsonl not written")
            rows = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
            sign_rows = [r for r in rows if r.get("action") == "sign"
                         and "SOUL-DOCUMENT" in r.get("artifact", "")]
            self.assertTrue(sign_rows, f"no SOUL-DOCUMENT sign row in audit: {rows}")
            self.assertEqual(sign_rows[0]["role"], "PE")
            self.assertEqual(sign_rows[0]["verdict"], "APPROVED")
            self.assertEqual(sign_rows[0]["wave"], "07")

    def test_placeholder_artifact_blocks_gate_advance(self):
        """0.6 fail-closed: a gate artifact that is still unfilled template
        boilerplate (double-brace tokens, TODO, etc.) cannot be signed -- a valid
        hash over placeholder text is not a valid artifact."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            soul = root / "core" / "governance" / "Governance" / "SOUL-DOCUMENT.md"
            soul.parent.mkdir(parents=True, exist_ok=True)
            soul.write_text(
                "# Soul Document\n\nPurpose: {{fill this in}}\nTODO: write it\n",
                encoding="utf-8",
            )
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            events = []
            orch = GateOrchestrator(
                root, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                prompt="build it",
            )
            orch.start()
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "sign-failed")       # placeholder blocked
            self.assertEqual(orch.state.current_gate, "G0")      # did not advance
            self.assertNotIn("G0", orch.state.signed)

    def test_missing_artifact_blocks_gate_advance(self):
        """0.1 fail-closed: a gate whose expected artifacts are ALL missing on
        disk cannot be approved/advanced. Before this fix, _default_sign signed
        nothing, raised nothing, and the gate advanced anyway (a fail-open)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            events = []
            # No gate artifacts seeded; _EndAdapter writes none. Real sign path
            # (no sign_fn) so _default_sign's artifact check is exercised.
            orch = GateOrchestrator(
                root, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                prompt="build it",
            )
            orch.start()
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "sign-failed")       # did not sign
            self.assertEqual(orch.state.current_gate, "G0")      # did not advance
            self.assertNotIn("G0", orch.state.signed)
            self.assertTrue(any(e.get("type") == "error" for e in events))

    def test_waive_marks_delivery_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            # waive G0, then approve the rest through to completion
            orch.apply_verdict("waive", "n/a for MVP")
            res = None
            for _ in range(5):
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "complete")
            self.assertFalse(res["ready"])               # INV-1: cannot be ready
            self.assertIn("G0", res["waived"])
            done = [e for e in events if e.get("type") == "delivery_complete"]
            self.assertTrue(done and done[-1]["ready"] is False)
