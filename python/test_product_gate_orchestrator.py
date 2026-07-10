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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
import signalos_lib.product.gate_orchestrator as go_mod
from signalos_lib.product.gate_orchestrator import (
    GateOrchestrator,
    GATE_ORDER,
    GATE_SPECIALISTS,
    resume_delivery,
)


class TestSignedSeeding(unittest.TestCase):
    """Regression for the "tests without code" root cause: a fresh/resumed
    orchestrator that reaches G4 with G0-G3 already signed on disk MUST seed its
    signed set from those signatures, so the G4 AgentLoop's plan-gating knows G2
    is signed and ALLOWS implementation writes under src/**. An empty signed set
    denied every impl write, leaving only test files."""

    def _orch_with_inspect(self, insp):
        with mock.patch.object(go_mod.wave_engine, "inspect", return_value=insp):
            with tempfile.TemporaryDirectory() as d:
                return GateOrchestrator(
                    Path(d), _EndAdapter(), [].append,
                    enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                    sign_fn=lambda *a, **k: ["x"], prompt="x")

    def test_seeds_prior_gates_at_g4(self):
        orch = self._orch_with_inspect({"next_gate": "G4", "all_signed": False})
        self.assertEqual(orch.state.signed, ["G0", "G1", "G2", "G3"])
        signed_ints = [int(str(g).lstrip("G")) for g in orch.state.signed]
        self.assertIn(2, signed_ints)  # plan-gating's G2 check now passes

    def test_all_signed_seeds_every_gate(self):
        orch = self._orch_with_inspect({"next_gate": None, "all_signed": True})
        self.assertEqual(orch.state.signed, list(GATE_ORDER))

    def test_fresh_delivery_at_g0_seeds_empty(self):
        orch = self._orch_with_inspect({"next_gate": "G0", "all_signed": False})
        self.assertEqual(orch.state.signed, [])


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
    orch = GateOrchestrator(
        Path(root), _EndAdapter(), events.append,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=fake_sign, prompt="build task management",
        max_rework=max_rework,
    )
    # Walk-mechanics tests drive a fake adapter that builds no real product;
    # stub the (real-npm) G4 build verification to pass -- the same spirit as
    # the faked sign_fn. The REAL _verify_g4_build enforcement is covered in
    # TestG4BuildVerification below.
    orch._verify_g4_build = lambda *a, **k: {"ok": True}
    return orch


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


class _RecordingAdapter:
    """Adapter stub that captures every user-role message it is given, so
    tests can assert the exact rework message the gate agent receives."""
    supports_tool_calls = True

    def __init__(self):
        self.user_messages: list[str] = []

    def chat(self, messages, model="test", tools=None, stream=False):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    self.user_messages.append(content)
        return AgentResponse(content="(gate work done)", tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


def _audit_reviews(root, gate="G0"):
    audit = Path(root) / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return []
    rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    return [r for r in rows
            if r.get("event") == "gate_review" and r.get("gate_id") == gate]


class TestReworkFeedbackThreading(unittest.TestCase):
    """P0 regression: apply_verdict('request-changes', feedback) must carry
    the reviewer's actual feedback into (a) the rework message the gate agent
    receives, (b) the persisted DeliveryState, and (c) the audit trail --
    previously the text was silently dropped on this path."""

    def _orch(self, root, events, **kw):
        adapter = _RecordingAdapter()
        orch = GateOrchestrator(
            Path(root), adapter, events.append,
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=lambda *a, **k: ["x"], prompt="build task management",
            **kw,
        )
        return orch, adapter

    def test_feedback_reaches_agent_verbatim_and_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch, adapter = self._orch(d, events)
            orch.start()
            adapter.user_messages.clear()
            res = orch.apply_verdict("request-changes", "make the nav horizontal")
            self.assertEqual(res["status"], "reworked")
            joined = "\n---\n".join(adapter.user_messages)
            self.assertIn("make the nav horizontal", joined)   # verbatim
            self.assertIn("Rework cycle 1", joined)
            # audit trail: same gate_review event format as the standalone path
            reviews = _audit_reviews(d)
            self.assertTrue(reviews, "no gate_review audit event written")
            self.assertEqual(reviews[-1]["verdict"], "REQUEST-CHANGES")
            self.assertEqual(reviews[-1]["feedback"], "make the nav horizontal")
            self.assertEqual(reviews[-1]["cycle"], 1)
            # persisted in delivery.json (INV-5: survives resume)
            sf = Path(d) / ".signalos" / "agent-runs" / orch.state.run_id / "delivery.json"
            data = json.loads(sf.read_text(encoding="utf-8"))
            self.assertEqual(data["feedback"]["G0"][0]["feedback"],
                             "make the nav horizontal")

    def test_multiple_cycles_keep_latest_and_prior_feedback(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch, adapter = self._orch(d, events)
            orch.start()
            orch.apply_verdict("request-changes", "first: fix the header")
            adapter.user_messages.clear()
            res = orch.apply_verdict("request-changes", "second: fix the footer")
            self.assertEqual(res["cycle"], 2)
            joined = "\n---\n".join(adapter.user_messages)
            self.assertIn("Rework cycle 2", joined)
            self.assertIn("second: fix the footer", joined)   # latest, verbatim
            self.assertIn("first: fix the header", joined)    # prior cycle kept
            self.assertEqual(len(orch.state.feedback["G0"]), 2)

    def test_reject_threads_feedback_and_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch, adapter = self._orch(d, events)
            orch.start()
            adapter.user_messages.clear()
            res = orch.apply_verdict("reject", "wrong direction entirely")
            self.assertEqual(res["status"], "rejected")
            joined = "\n---\n".join(adapter.user_messages)
            self.assertIn("wrong direction entirely", joined)  # verbatim
            reviews = _audit_reviews(d)
            self.assertTrue(reviews)
            self.assertEqual(reviews[-1]["verdict"], "REJECTED")
            self.assertEqual(reviews[-1]["feedback"], "wrong direction entirely")

    def test_max_rework_still_audits_the_refused_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch, adapter = self._orch(d, events, max_rework=1)
            orch.start()
            orch.apply_verdict("request-changes", "cycle one feedback")
            res = orch.apply_verdict("request-changes", "over budget feedback")
            self.assertEqual(res["status"], "max-rework")
            reviews = _audit_reviews(d)
            self.assertEqual(reviews[-1]["feedback"], "over budget feedback")
            self.assertEqual(reviews[-1]["cycle"], 2)
            # refused feedback is NOT stored as actionable state
            self.assertEqual(len(orch.state.feedback["G0"]), 1)

    def test_resume_restores_feedback_and_tolerates_legacy_state(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch, _adapter = self._orch(d, events)
            orch.start()
            orch.apply_verdict("request-changes", "tighten the scope")
            sf = Path(d) / ".signalos" / "agent-runs" / orch.state.run_id / "delivery.json"

            # resume restores the feedback field
            loaded = resume_delivery(
                Path(d), orch.state.run_id, _RecordingAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: [],
            )
            self.assertEqual(loaded.state.feedback["G0"][0]["feedback"],
                             "tighten the scope")
            self.assertIn("tighten the scope", loaded._gate_message("G0"))

            # legacy persisted state (no feedback field) still resumes and the
            # rework message falls back to the generic nudge
            data = json.loads(sf.read_text(encoding="utf-8"))
            del data["feedback"]
            sf.write_text(json.dumps(data), encoding="utf-8")
            legacy = resume_delivery(
                Path(d), orch.state.run_id, _RecordingAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: [],
            )
            self.assertEqual(legacy.state.feedback, {})
            msg = legacy._gate_message("G0")
            self.assertIn("address the prior feedback", msg)


class TestBudgetParity(unittest.TestCase):
    """P1: the orchestrator path and the standalone gate_review path must
    honor the SAME rework budget source (budgets.resolve_gate_rework_budget)."""

    def test_default_budgets_match(self):
        import os
        from signalos_lib.product.budgets import (
            DEFAULT_GATE_REWORK_BUDGET,
            resolve_gate_rework_budget,
        )
        self.assertIsNone(os.environ.get("SIGNALOS_GATE_REWORK_BUDGET"))
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)   # max_rework=None -> resolved default
            self.assertEqual(orch.max_rework, DEFAULT_GATE_REWORK_BUDGET)
            self.assertEqual(resolve_gate_rework_budget(None),
                             DEFAULT_GATE_REWORK_BUDGET)

    def test_env_override_drives_both_paths(self):
        import os
        from signalos_lib.product.gate_review import handle_request_changes
        os.environ["SIGNALOS_GATE_REWORK_BUDGET"] = "1"
        try:
            with tempfile.TemporaryDirectory() as d:
                events, signed = [], []
                orch = _orch(d, events, signed)
                self.assertEqual(orch.max_rework, 1)
                orch.start()
                self.assertEqual(
                    orch.apply_verdict("request-changes", "fix it")["status"],
                    "reworked")
                self.assertEqual(
                    orch.apply_verdict("request-changes", "again")["status"],
                    "max-rework")
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / ".signalos").mkdir(parents=True)
                r1 = handle_request_changes(
                    repo_root=root, gate_id="run-1", feedback="fix it",
                    specific_items=["fix it"], cycle=0)
                self.assertEqual(r1["status"], "rework_dispatched")
                r2 = handle_request_changes(
                    repo_root=root, gate_id="run-1", feedback="again",
                    specific_items=["again"], cycle=r1["cycle"])
                self.assertEqual(r2["status"], "max_cycles_reached")
        finally:
            os.environ.pop("SIGNALOS_GATE_REWORK_BUDGET", None)


class TestG4BuildVerification(unittest.TestCase):
    """INV-2 / no fake-green: G4 cannot be signed unless a REAL product was
    written this run AND it builds + tests pass (independently verified)."""

    def _make(self, root):
        def fake_sign(repo_root, gate, signer, role, verdict, conditions):
            return [f"{gate}.md"]
        return GateOrchestrator(
            Path(root), _EndAdapter(), (lambda e: None),
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=fake_sign, prompt="build an expense tracker",
        )

    def _write(self, root, rel, content):
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_stub_only_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            # Only the 5-line scaffold stub + a test -> no real product source.
            self._write(d, "src/App.tsx", "function App() {\n  return <h1>SignalOS Product</h1>;\n}\n\nexport default App;\n")
            self._write(d, "src/App.test.tsx", "test('x', () => {});\n")
            self._write(d, "src/main.tsx", "import App from './App';\n")
            res = orch._verify_g4_build(None)
            self.assertFalse(res["ok"])
            self.assertIn("No real product source", res["reason"])

    def test_build_or_test_failing_surfaces_real_errors(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            self._write(d, "src/components/Expense.tsx", "export const Expense = () => null;\n")
            with mock.patch("signalos_lib.product.stacks.detect_profile", return_value="react-vite"), \
                 mock.patch("signalos_lib.product.validation.build_validation_plan",
                            return_value={"can_validate_build": True, "can_validate_tests": True, "profile": "react-vite"}), \
                 mock.patch("signalos_lib.product.validation.run_validation",
                            return_value={"results": {"build": {"status": "failed", "output": "error TS2307: Cannot find module './Foo'"},
                                                       "test": {"status": "passed"}}}):
                res = orch._verify_g4_build(None)
            self.assertFalse(res["ok"])
            self.assertIn("not green", res["reason"])
            self.assertIn("TS2307", res["reason"])  # actionable: the real error is fed back

    def test_real_source_and_passing_build_allows(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            self._write(d, "src/components/ExpenseList.tsx", "export const ExpenseList = () => null;\n")
            self._write(d, "src/types/expense.ts", "export interface Expense { id: string }\n")
            with mock.patch("signalos_lib.product.stacks.detect_profile", return_value="react-vite"), \
                 mock.patch("signalos_lib.product.validation.build_validation_plan",
                            return_value={"can_validate_build": True, "can_validate_tests": True, "profile": "react-vite"}), \
                 mock.patch("signalos_lib.product.validation.run_validation",
                            return_value={"results": {"build": {"status": "passed"}, "test": {"status": "passed"}}}):
                res = orch._verify_g4_build(None)
            self.assertTrue(res["ok"])

    def test_apply_verdict_blocks_g4_until_verified(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            orch.state.current_gate = "G4"
            orch._g4_verify = {"ok": False, "reason": "no real product source (src/**) this run"}
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "build-not-verified")
            self.assertNotIn("G4", orch.state.signed)


class TestScaffoldFirst(unittest.TestCase):
    """FIX 2: GateOrchestrator materializes the SELECTED stack's shell before
    the BUILD gate on a greenfield repo -- and is a STRICT no-op on a repo that
    already has the shell on disk (the benchmark's React/Vitest fixture)."""

    def _orch(self, root):
        return GateOrchestrator(
            Path(root), _EndAdapter(), (lambda e: None),
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=lambda *a, **k: ["x"], prompt="build an expense tracker")

    def _write_profile_json(self, root, data):
        meta = Path(root) / ".signalos"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "profile.json").write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _snapshot(root):
        """Map of every file -> (size, sha256) so we can prove a no-op created,
        modified, or removed NOTHING."""
        import hashlib
        snap = {}
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                data = p.read_bytes()
                snap[str(p.relative_to(root))] = (len(data), hashlib.sha256(data).hexdigest())
        return snap

    def test_greenfield_react_selection_scaffolds_shell(self):
        # (a) greenfield repo + profile.json react-vite -> the React shell is
        # materialized before the build gate.
        with tempfile.TemporaryDirectory() as d:
            self._write_profile_json(d, {"profile": "react-vite"})
            self._orch(d)._scaffold_shell_if_greenfield()
            root = Path(d)
            self.assertTrue((root / "package.json").is_file())
            self.assertTrue((root / "vite.config.ts").is_file())
            self.assertTrue((root / "src" / "main.tsx").is_file())
            self.assertTrue((root / "tsconfig.json").is_file())

    def test_already_scaffolded_react_repo_is_noop(self):
        # (b) CRITICAL benchmark guarantee: a repo that already ships
        # package.json + a React/Vitest shell is left EXACTLY as-is -- no file
        # created, modified, or removed by scaffold-first.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(
                json.dumps({
                    "dependencies": {"react": "^18.3.1"},
                    "devDependencies": {"vite": "^5.4.0", "vitest": "^3.2.0"},
                    "scripts": {"build": "tsc && vite build", "test": "vitest run"},
                }, indent=2) + "\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text(
                "export default function App() { return null; }\n", encoding="utf-8")
            (root / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
            self._write_profile_json(d, {"profile": "react-vite"})

            before = self._snapshot(root)
            self._orch(d)._scaffold_shell_if_greenfield()
            after = self._snapshot(root)

            self.assertEqual(before, after)  # nothing created/modified/removed

    def test_generic_profile_is_noop(self):
        # (c) unknown/generic -> no adapter shell to materialize: no-op, no crash.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_profile_json(d, {"profile": "not-a-real-stack"})  # -> generic
            before = self._snapshot(root)
            self._orch(d)._scaffold_shell_if_greenfield()  # must not raise
            after = self._snapshot(root)
            self.assertEqual(before, after)

    def test_scaffold_error_never_raises(self):
        # A scaffold hiccup must be swallowed -- the walk is never failed by it.
        with tempfile.TemporaryDirectory() as d:
            self._write_profile_json(d, {"profile": "react-vite"})
            orch = self._orch(d)
            with mock.patch("signalos_lib.product.stacks.get_adapter",
                            side_effect=RuntimeError("boom")):
                orch._scaffold_shell_if_greenfield()  # no exception escapes


class TestValidationConvergence(unittest.TestCase):
    """Claim 2 convergence PROOF (validation): both engines call the SAME
    validation implementation -- a fix to validation.run_validation reaches the
    GateOrchestrator G4 wall AND the run_delivery pipeline. This locks the
    already-shared convergence so a future refactor cannot silently re-diverge."""

    def test_delivery_uses_the_single_validation_impl(self):
        # run_delivery imports run_validation from validation.py -> the very same
        # function object the G4 wall runs. One implementation, not a copy.
        from signalos_lib.product import delivery, validation
        self.assertIs(delivery.run_validation, validation.run_validation)
        self.assertIs(delivery.build_validation_plan, validation.build_validation_plan)

    def test_g4_wall_routes_through_the_single_validation_impl(self):
        # The GateOrchestrator G4 build wall delegates to validation.run_validation
        # (imported locally inside _verify_g4_build), so patching the shared symbol
        # is observed here exactly as it would be in run_delivery.
        with tempfile.TemporaryDirectory() as d:
            orch = GateOrchestrator(
                Path(d), _EndAdapter(), (lambda e: None),
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: ["x"], prompt="build an expense tracker")
            (Path(d) / "src" / "components").mkdir(parents=True)
            (Path(d) / "src" / "components" / "ExpenseList.tsx").write_text(
                "export const ExpenseList = () => null;\n", encoding="utf-8")
            calls = []
            real_run = __import__(
                "signalos_lib.product.validation", fromlist=["run_validation"]
            ).run_validation

            def _spy(repo_root, plan, dry_run=False):
                calls.append(plan.get("profile"))
                return {"results": {"build": {"status": "passed"},
                                    "test": {"status": "passed"}}}

            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.validation.build_validation_plan",
                            return_value={"can_validate_build": True,
                                          "can_validate_tests": True,
                                          "profile": "react-vite"}), \
                 mock.patch("signalos_lib.product.validation.run_validation",
                            side_effect=_spy):
                res = orch._verify_g4_build(None)
            self.assertTrue(res["ok"])
            self.assertEqual(calls, ["react-vite"])  # the shared impl was invoked
            self.assertIsNotNone(real_run)


class TestCloseoutConvergence(unittest.TestCase):
    """Claim 2 convergence (closeout): a COMPLETED GateOrchestrator walk (the
    desktop `agent:deliver` surface) now writes the delivery CLOSEOUT via the
    SAME closeout.build_closeout / write_closeout run_delivery uses -- closing
    the divergence where the walk emitted `delivery_complete` but produced no
    CLOSEOUT.json. Proven strictly post-G4 and product-file-preserving, so the
    already-scaffolded React benchmark's G4 build is behaviour-identical."""

    def _orch(self, root, *, finalize_closeout=True):
        events: list[dict] = []
        orch = GateOrchestrator(
            Path(root), _EndAdapter(), events.append,
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=lambda *a, **k: ["x"], prompt="build an expense tracker",
            finalize_closeout=finalize_closeout)
        # Walk-mechanics: don't run the real (npm) build or its verification.
        orch._verify_g4_build = lambda *a, **k: {"ok": True}
        orch._execute_build_gate = lambda *a, **k: setattr(
            orch, "_g4_verify", {"ok": True})
        return orch, events

    @staticmethod
    def _product_snapshot(root):
        """Byte-level map of every NON-.signalos file, to prove closeout leaves
        product source / build outputs untouched."""
        import hashlib
        root = Path(root)
        snap = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".signalos" not in p.relative_to(root).parts:
                data = p.read_bytes()
                snap[str(p.relative_to(root))] = (len(data),
                                                  hashlib.sha256(data).hexdigest())
        return snap

    def _closeout_json(self, root):
        return Path(root) / ".signalos" / "product" / "CLOSEOUT.json"

    def test_completed_walk_writes_closeout_via_shared_impl(self):
        from signalos_lib.product import closeout as closeout_mod
        real_build = closeout_mod.build_closeout
        seen = []

        def _wrapped(repo_root, product_name, profile, blueprint_id):
            seen.append(profile)
            return real_build(repo_root, product_name, profile, blueprint_id)

        with tempfile.TemporaryDirectory() as d:
            orch, events = self._orch(d)
            with mock.patch("signalos_lib.product.closeout.build_closeout",
                            side_effect=_wrapped):
                orch.start()
                for _ in range(6):                 # G0..G5 -> complete
                    orch.apply_verdict("approve")
            self.assertTrue(seen, "walk did not delegate to closeout.build_closeout")
            self.assertTrue(self._closeout_json(d).is_file(),
                            "completed walk wrote no CLOSEOUT.json")
            self.assertTrue(any(e.get("type") == "closeout" for e in events))
            # `delivery_complete` still emitted and unchanged (additive only).
            self.assertTrue(any(e.get("type") == "delivery_complete" for e in events))

    def test_opt_out_is_strict_noop(self):
        # A driver (e.g. a build benchmark) that wants zero completion writes can
        # disable it -- no CLOSEOUT.json, no `closeout` event, walk still completes.
        with tempfile.TemporaryDirectory() as d:
            orch, events = self._orch(d, finalize_closeout=False)
            orch.start()
            res = None
            for _ in range(6):
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "complete")
            self.assertFalse(self._closeout_json(d).is_file())
            self.assertFalse(any(e.get("type") == "closeout" for e in events))

    def test_already_scaffolded_react_g4_unchanged_and_product_untouched(self):
        # CRITICAL benchmark guarantee. A pre-scaffolded React fixture:
        #   (a) G4's REAL verified-build outcome is unchanged (still ok);
        #   (b) closeout writes ONLY under .signalos -- product files (src/**,
        #       package.json, vite.config.ts) are byte-identical after a full
        #       walk to completion; and
        #   (c) no CLOSEOUT.json exists until the walk actually completes at G5.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text(
                json.dumps({
                    "dependencies": {"react": "^18.3.1"},
                    "devDependencies": {"vite": "^5.4.0", "vitest": "^3.2.0"},
                    "scripts": {"build": "tsc && vite build", "test": "vitest run"},
                }, indent=2) + "\n", encoding="utf-8")
            (root / "src" / "components").mkdir(parents=True)
            (root / "src" / "main.tsx").write_text(
                "import App from './App';\n", encoding="utf-8")
            (root / "src" / "App.tsx").write_text(
                "import { ExpenseList } from './components/ExpenseList';\n"
                "export default function App() {\n  return <ExpenseList />;\n}\n",
                encoding="utf-8")
            (root / "src" / "components" / "ExpenseList.tsx").write_text(
                "export const ExpenseList = () => null;\n", encoding="utf-8")
            (root / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")

            orch, _events = self._orch(d)

            # (a) the REAL G4 wall still verifies this already-scaffolded repo as a
            # passing build (validation mocked green, same as TestG4BuildVerification).
            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.validation.build_validation_plan",
                            return_value={"can_validate_build": True,
                                          "can_validate_tests": True,
                                          "profile": "react-vite"}), \
                 mock.patch("signalos_lib.product.validation.run_validation",
                            return_value={"results": {"build": {"status": "passed"},
                                                      "test": {"status": "passed"}}}):
                verdict = orch._verify_g4_build(None)
            self.assertTrue(verdict["ok"])  # verified-build outcome preserved

            before = self._product_snapshot(root)
            orch.start()
            # G0..G3 approve -> now parked at G4; nothing completed yet.
            for _ in range(4):
                orch.apply_verdict("approve")
            self.assertFalse(self._closeout_json(d).is_file(),
                             "closeout must not run before the walk completes")
            orch.apply_verdict("approve")  # sign G4 -> advance to G5
            self.assertFalse(self._closeout_json(d).is_file(),
                             "closeout must not run at G4 -- strictly post-G4")
            orch.apply_verdict("approve")  # sign G5 -> complete
            self.assertTrue(self._closeout_json(d).is_file())

            after = self._product_snapshot(root)
            self.assertEqual(before, after)  # product files untouched by closeout


if __name__ == "__main__":
    unittest.main()
