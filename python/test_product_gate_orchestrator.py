"""Gate orchestration tests (T26-T38): the G0->G5 walk, verdict handling,
sign-on-approve (INV-3), bounded rework/reject, G3 preview, persistence.

Deterministic: an end-turn adapter (no provider/network) + a recording
sign_fn double (so INV-3's sign.py call is asserted without needing real
gate artifacts on disk).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
    GATE_TASK_FRAMING,
    resume_delivery,
)
from signalos_lib.sign import (
    SOLO_FOUNDER_GATE0_CONSENT,
    approve_gate0_as_solo_founder,
)


def _explicit_g0_approval(root: Path, approval_id: str = "test-g0") -> dict:
    signalos = root / ".signalos"
    signalos.mkdir(parents=True, exist_ok=True)
    (signalos / "identity.json").write_text(
        json.dumps({"name": "Test Founder", "role": "PO"}),
        encoding="utf-8",
    )
    return approve_gate0_as_solo_founder(
        root,
        consent=SOLO_FOUNDER_GATE0_CONSENT,
        via="simulation",
        expected_workspace=str(root),
        approval_id=approval_id,
        project_id="default",
        expected_project_id="default",
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


class _ProviderTimeoutAdapter:
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        raise TimeoutError("provider connection timed out")


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


def test_g1_reviewable_when_brainstorm_authored_but_belief_carried_from_g0():
    # Regression (funded canary run 4): G1's SIGNING manifest (BELIEF +
    # ROLE_ACTIVATION_CARD) is drafted by the G0 onboarding agent and signed
    # at G1, so the G1 brainstorm agent never (re)writes a manifest artifact --
    # it authors core/strategy/brainstorm/wave-N-brainstorm.md. The strict
    # freshness check falsely blocked G1 as "stale outputs" even though the
    # agent did real work. Only G1 is carried-forward; every other gate keeps
    # strict manifest-freshness.
    import time as _time
    with tempfile.TemporaryDirectory() as d:
        events, signed = [], []
        orch = _orch(d, events, signed)
        orch._gate_run_started_at = _time.time()
        # Carried-forward manifest present but stale (drafted "at G0"):
        from signalos_lib import artifacts
        for rel in ("core/strategy/BELIEF.md",
                    "core/execution/ROLE_ACTIVATION_CARD.md"):
            p = artifacts.resolve_workspace_path(Path(d), rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("drafted at G0\n", encoding="utf-8")
            old = orch._gate_run_started_at - 3600
            os.utime(p, (old, old))
        # No brainstorm work product yet -> still blocked (anti-stale-green).
        assert orch._gate_agent_produced_fresh_work("G1") is False
        # The brainstorm agent authors its own product this run -> reviewable.
        bs = artifacts.resolve_workspace_path(
            Path(d), "core/strategy/brainstorm/wave-1-brainstorm.md")
        bs.parent.mkdir(parents=True, exist_ok=True)
        bs.write_text("## Hypotheses\n", encoding="utf-8")
        assert orch._gate_agent_produced_fresh_work("G1") is True
        # A non-carried-forward gate never gets this relaxation.
        assert orch._gate_agent_produced_fresh_work("G2") is False


def _completed_result(**kw):
    from types import SimpleNamespace
    base = dict(status="completed", failure_type="", error="", wrote_no_files=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_gate_completion_reprompt_retries_incomplete_then_blocks_bounded():
    # Regression (funded canary run 7): the model completed its G2 turn 90%
    # done (used test: fields) but left artifacts missing; the gate blocked
    # instead of feeding the exact gaps back. Now a completed-but-incomplete
    # gate gets bounded corrective reprompts before blocking.
    with tempfile.TemporaryDirectory() as d:
        events, signed = [], []
        orch = _orch(d, events, signed)
        seen = []

        def fake_exec(gate, system_prompt, signed_ints):
            seen.append(getattr(orch, "_gate_correction", None))
            return _completed_result()

        orch._gate_executor = lambda gate: fake_exec
        orch._gate_review_ready = lambda gate, result: {
            "ok": False, "reason": "ACCEPTANCE_CRITERIA.md missing"}
        orch._run_gate("G2")

        assert len(seen) == orch.MAX_GATE_COMPLETION_RETRIES + 1
        assert seen[0] is None
        assert all("ACCEPTANCE_CRITERIA.md missing" in (c or "") for c in seen[1:])
        retries = [e for e in events if e.get("type") == "gate_completion_retry"]
        assert len(retries) == orch.MAX_GATE_COMPLETION_RETRIES
        assert orch.state.status == "blocked"


def test_gate_completion_reprompt_stops_once_the_gate_completes():
    with tempfile.TemporaryDirectory() as d:
        events, signed = [], []
        orch = _orch(d, events, signed)
        calls = {"n": 0}

        def fake_exec(gate, system_prompt, signed_ints):
            calls["n"] += 1
            return _completed_result()

        orch._gate_executor = lambda gate: fake_exec
        orch._gate_review_ready = lambda gate, result: {
            "ok": calls["n"] >= 2, "reason": "PLAN.md missing"}
        orch._run_gate("G2")

        assert calls["n"] == 2  # one corrective retry, then reviewable
        assert orch.state.status == "awaiting-verdict"


def test_gate_completion_reprompt_never_retries_terminal_outcomes():
    # A refusal/error/stall (status != completed) is terminal -- no reprompt.
    with tempfile.TemporaryDirectory() as d:
        events, signed = [], []
        orch = _orch(d, events, signed)
        calls = {"n": 0}

        def fake_exec(gate, system_prompt, signed_ints):
            calls["n"] += 1
            return _completed_result(status="error", failure_type="infrastructure",
                                     error="boom")

        orch._gate_executor = lambda gate: fake_exec
        orch._gate_review_ready = lambda gate, result: {
            "ok": False, "reason": "agent did not complete the gate"}
        orch._run_gate("G2")

        assert calls["n"] == 1
        assert orch.state.status == "blocked"


def test_gate_message_frames_the_founder_prompt_per_gate():
    # Regression (funded canary): the raw "build X" founder prompt reached a
    # narrowly-scoped governance seat verbatim; a literal model at G0 read it
    # as "build X now", declared it out of scope, and refused the delivery.
    # Every governance gate now prefixes one line saying what THIS gate
    # produces for X; G4 keeps its dedicated build directive.
    with tempfile.TemporaryDirectory() as d:
        events, signed = [], []
        orch = _orch(d, events, signed)
        for gate, framing in GATE_TASK_FRAMING.items():
            msg = orch._gate_message(gate)
            assert msg.startswith(framing), gate
            assert "build task management" in msg, gate
        g4 = orch._gate_message("G4")
        assert GATE_TASK_FRAMING["G0"] not in g4
        assert "build task management" in g4


def _orch(root, events, signed, *, max_rework=None, release_ready=True):
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
    # Fix 1: the _EndAdapter never calls a tool, so the AgentLoop honestly
    # reports `stalled_no_tool` -- which the new outcome gate refuses to open
    # for review. These walk-mechanics tests simulate a SUCCESSFUL agent, so
    # stub the outcome gate open (mirrors the _verify_g4_build stub). The REAL
    # _gate_review_ready enforcement is covered in TestAgentOutcomeGate below.
    orch._gate_review_ready = lambda *a, **k: {"ok": True}
    if release_ready:
        # Walk-mechanics tests use a fake signer/build and do not construct real
        # release evidence. C7's verify-before-sign contract has dedicated tests.
        orch._verify_g5_release = lambda **k: {"ok": True, "reasons": []}
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
            # Simulate process loss: the old instance no longer owns the
            # workspace lock before a new orchestrator reconstructs it.
            orch._release_delivery_lock()

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
                                         "args": [_json.dumps({"run_id": "del-1", "gate_id": "G0", "verdict": "approve"})],
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

        signed = []
        srv._AGENT_ADAPTER_FACTORY = lambda model: _EndAdapter()
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(trust_tier="T3")
        srv._DELIVERY_SIGN_FN = lambda root, gate, signer, role, verdict, conditions: signed.append(gate) or [f"{gate}.md"]
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
                    srv._ACTIVE_DELIVERIES["del-resume"]._release_delivery_lock()
                    srv._ACTIVE_DELIVERIES.clear()

                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r2 = srv.handle({"command": "agent:resume",
                                         "args": [_json.dumps({"run_id": "del-resume", "provider": "openai", "model": "gpt-test"})],
                                         "id": "2"})
                    self.assertTrue(r2["ok"], r2)
                    self.assertTrue(r2["data"]["resumed"])
                    self.assertEqual(r2["data"]["gate"], "G0")
                    self.assertEqual(r2["data"]["status"], "blocked")
                    events = [_json.loads(l) for l in buf.getvalue().splitlines() if l.strip().startswith("{")]
                    self.assertTrue(any(e.get("type") == "gate_blocked" and e.get("gate") == "G0" for e in events))
                    self.assertIn("del-resume", srv._ACTIVE_DELIVERIES)

                    # A restart must not turn an unreviewable checkpoint into a
                    # signable gate, even when IPC injects a custom signer.
                    r3 = srv.handle({"command": "agent:verdict",
                                     "args": [_json.dumps({"run_id": "del-resume", "gate_id": "G0", "verdict": "approve"})],
                                     "id": "3"})
                    self.assertTrue(r3["ok"], r3)
                    self.assertEqual(r3["data"]["status"], "not-reviewable")
                    self.assertEqual(signed, [])
                    persisted = _json.loads((Path(d) / ".signalos" / "agent-runs"
                                             / "del-resume" / "delivery.json").read_text(
                                                 encoding="utf-8"))
                    self.assertEqual(persisted["status"], "blocked")
                finally:
                    os.chdir(cwd)
        finally:
            srv._AGENT_ADAPTER_FACTORY = None
            srv._AGENT_ENFORCEMENT_FACTORY = None
            srv._DELIVERY_SIGN_FN = None
            srv._ACTIVE_DELIVERIES.clear()


def _seed_g0_artifacts(root: Path) -> Path:
    """Seed ALL FOUR real G0 manifest artifacts with signable, non-placeholder
    content. Fix 2 fail-closed: _default_sign now requires every required
    artifact present, so a real-sign G0 test must materialize all of them (the
    old tests seeded just SOUL-DOCUMENT -- which encoded the 1-of-4 fail-open).
    Returns the SOUL-DOCUMENT path for tests that assert on it."""
    gov = root / "core" / "governance" / "Governance"
    gov.mkdir(parents=True, exist_ok=True)
    files = {
        "SOUL-DOCUMENT.md": "# Soul Document\n\nThe product purpose.\n",
        "CONSTITUTION.md": "# Constitution\n\nThe operating rules.\n",
        "SURFACE_INVENTORY.md": "# Surface Inventory\n\nThe surfaces.\n",
        "PERMANENTLY_T3.md": "# Permanently T3\n\nThe permanent tier notes.\n",
    }
    for name, text in files.items():
        (gov / name).write_text(text, encoding="utf-8")
    return gov / "SOUL-DOCUMENT.md"


class TestRealSignAuditAndWaive(unittest.TestCase):
    """T38: the REAL sign.py path writes the audit trail (no fake signer).
    T37: a waived gate makes the delivery close not-'ready' (INV-1)."""

    def test_real_sign_writes_audit_trail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Seed ALL FOUR real G0 artifacts (Fix 2: all required present).
            soul = _seed_g0_artifacts(root)
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
            # Fix 1: the _EndAdapter stalls (no tool calls), so the outcome gate
            # would keep G0 blocked. This test exercises the real SIGN path, not
            # the outcome gate, so simulate a successful agent (as with the fake
            # sign_fn in the walk-mechanics tests). Outcome-gate enforcement is
            # covered by TestAgentOutcomeGate.
            orch._gate_review_ready = lambda *a, **k: {"ok": True}
            orch.start()
            self.assertTrue(_explicit_g0_approval(root)["signed"])
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

    def test_placeholder_artifact_blocks_gate_advance(self):
        """0.6 fail-closed: a gate artifact that is still unfilled template
        boilerplate (double-brace tokens, TODO, etc.) cannot be signed -- a valid
        hash over placeholder text is not a valid artifact."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # All four G0 artifacts present (Fix 2), but SOUL-DOCUMENT still
            # carries unresolved template placeholders -> the sign path must
            # refuse on the placeholder, not on a missing artifact.
            soul = _seed_g0_artifacts(root)
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
            orch._gate_review_ready = lambda *a, **k: {"ok": True}  # isolate the sign path
            orch.start()
            approval = _explicit_g0_approval(root, "placeholder-g0")
            self.assertFalse(approval["signed"])
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "explicit-approval-required")
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
            # Isolate _default_sign's all-artifacts check: simulate a successful
            # agent so we reach the sign path (which must still refuse on the
            # missing artifacts).
            orch._gate_review_ready = lambda *a, **k: {"ok": True}
            orch.start()
            approval = _explicit_g0_approval(root, "missing-g0")
            self.assertFalse(approval["signed"])
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "explicit-approval-required")
            self.assertEqual(orch.state.current_gate, "G0")      # did not advance
            self.assertNotIn("G0", orch.state.signed)
            self.assertTrue(any(e.get("type") == "gate_blocked" for e in events))

    def test_waive_marks_delivery_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed, release_ready=False)
            orch.start()
            # Waive G0, then reach G5. C7 refuses the release before signing;
            # a known-not-ready product must never complete or push.
            orch.apply_verdict("waive", "n/a for MVP")
            res = None
            for _ in range(5):
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "release-not-ready")
            self.assertFalse(res["ready"])
            self.assertIn("G0", orch.state.waived)
            self.assertNotIn("G5", orch.state.signed)
            done = [e for e in events if e.get("type") == "delivery_complete"]
            self.assertFalse(done)


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
            orch._release_delivery_lock()
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
            loaded._release_delivery_lock()
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
        orch = GateOrchestrator(
            Path(root), _EndAdapter(), (lambda e: None),
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=fake_sign, prompt="build an expense tracker",
        )
        orch._product_source_dir = lambda: "src"
        orch._prepare_g4_attribution()
        return orch

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
            res = orch._verify_g4_build(_Result())
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
                res = orch._verify_g4_build(_Result())
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
                res = orch._verify_g4_build(_Result())
            self.assertTrue(res["ok"])

    def test_apply_verdict_blocks_g4_until_verified(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            orch.state.current_gate = "G4"
            orch._g4_verify = {"ok": False, "reason": "no real product source (src/**) this run"}
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "build-not-verified")
            self.assertNotIn("G4", orch.state.signed)


class TestUxAcceptanceGate(unittest.TestCase):
    """UX/BEHAVIORAL acceptance is a G4 HARD gate (both profiles): a build with
    a green build AND green tests is STILL not verified when it ships no real,
    styled, usable UI (the measured-render acceptance fails). Enforced inside
    _verify_g4_build so it applies before G4 can be signed."""

    def _make(self, root):
        orch = GateOrchestrator(
            Path(root), _EndAdapter(), (lambda e: None),
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=lambda *a, **k: [f"{'G4'}.md"], prompt="build an expense tracker")
        orch._product_source_dir = lambda: "src"
        orch._prepare_g4_attribution()
        return orch

    def _write(self, root, rel, content):
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _green_validation(self):
        return (
            mock.patch("signalos_lib.product.stacks.detect_profile",
                       return_value="react-vite"),
            mock.patch("signalos_lib.product.validation.build_validation_plan",
                       return_value={"can_validate_build": True,
                                     "can_validate_tests": True,
                                     "profile": "react-vite"}),
            mock.patch("signalos_lib.product.validation.run_validation",
                       return_value={"results": {"build": {"status": "passed"},
                                                 "test": {"status": "passed"}}}),
        )

    def _seed_wired_product(self, d):
        self._write(d, "src/App.tsx",
                    "import { List } from './components/List';\n"
                    "export default function App(){ return <List/>; }\n")
        self._write(d, "src/components/List.tsx",
                    "export const List = () => null;\n")

    def test_green_build_with_failing_ux_is_not_verified(self):
        # RED case: build + tests green, but the UI is bare HTML -> the UX
        # acceptance measurement fails -> G4 is NOT verified (cannot be signed).
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            self._seed_wired_product(d)
            p_prof, p_plan, p_run = self._green_validation()
            with p_prof, p_plan, p_run, \
                 mock.patch("signalos_lib.product.acceptance.run_ux_acceptance",
                            return_value={"ok": False, "ran": True,
                                          "reason": "UX acceptance FAILED -- the "
                                          "build does not ship a real, styled, "
                                          "usable UI."}):
                res = orch._verify_g4_build(_Result())
            self.assertFalse(res["ok"])
            self.assertIn("UX acceptance", res["reason"])
            self.assertEqual(res.get("ux"), "failed")

    def test_green_build_with_passing_ux_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            self._seed_wired_product(d)
            p_prof, p_plan, p_run = self._green_validation()
            with p_prof, p_plan, p_run, \
                 mock.patch("signalos_lib.product.acceptance.run_ux_acceptance",
                            return_value={"ok": True, "ran": True,
                                          "reason": "UX acceptance passed."}):
                res = orch._verify_g4_build(_Result())
            self.assertTrue(res["ok"])

    def test_ux_skip_never_false_fails_a_green_build(self):
        # When the UI cannot be measured offline (no installed deps etc.), the
        # gate SKIPS and does not block a genuinely-green build.
        with tempfile.TemporaryDirectory() as d:
            orch = self._make(d)
            self._seed_wired_product(d)
            p_prof, p_plan, p_run = self._green_validation()
            with p_prof, p_plan, p_run, \
                 mock.patch("signalos_lib.product.acceptance.run_ux_acceptance",
                            return_value={"ok": True, "ran": False,
                                          "reason": "deps not installed"}):
                res = orch._verify_g4_build(_Result())
            self.assertTrue(res["ok"])


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
            orch._product_source_dir = lambda: "src"
            orch._prepare_g4_attribution()
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
                res = orch._verify_g4_build(_Result())
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
        orch._gate_review_ready = lambda *a, **k: {"ok": True}
        orch._verify_g5_release = lambda **k: {"ok": True, "reasons": []}
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


class TestEngineProfiles(unittest.TestCase):
    """Panel decision: ONE engine, config-gated PROFILES (not two engines).
    The benchmark profile MUST be behavior-identical to today (no new blocking/
    flaky post-build stage); the production profile adds the release-safety
    stages -- a hard-blocking security gate and evidence-only runtime/UX proof --
    STRICTLY after the G4 build is verified so they can never move the score."""

    def _make(self, root, *, profile=None, signed=None):
        """Orchestrator + captured events. `signed` (if given) records every
        sign call so we can assert which gates were signed."""
        events: list[dict] = []

        def fake_sign(repo_root, gate, signer, role, verdict, conditions):
            if signed is not None:
                signed.append(gate)
            return [f"{gate}.md"]

        kw = {} if profile is None else {"profile": profile}
        orch = GateOrchestrator(
            Path(root), _EndAdapter(), events.append,
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=fake_sign, prompt="build an expense tracker", **kw)
        # Walk-mechanics: never run the real (npm) build / verification.
        orch._verify_g4_build = lambda *a, **k: {"ok": True}
        orch._execute_build_gate = lambda *a, **k: setattr(
            orch, "_g4_verify", {"ok": True})
        orch._gate_review_ready = lambda *a, **k: {"ok": True}
        orch._verify_g5_release = lambda **k: {"ok": True, "reasons": []}
        return orch, events

    @staticmethod
    def _product_snapshot(root):
        import hashlib
        root = Path(root)
        snap = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".signalos" not in p.relative_to(root).parts:
                data = p.read_bytes()
                snap[str(p.relative_to(root))] = (len(data),
                                                  hashlib.sha256(data).hexdigest())
        return snap

    # -- profile selection + safe default ----------------------------------

    def test_default_profile_is_the_safe_benchmark_profile(self):
        with tempfile.TemporaryDirectory() as d:
            orch, _ = self._make(d)  # no profile= -> default
            self.assertEqual(orch.profile, "benchmark")
            self.assertFalse(go_mod.PROFILE_STAGES["benchmark"]["security_gate"])
            self.assertFalse(go_mod.PROFILE_STAGES["benchmark"]["runtime_proof"])

    def test_unknown_profile_is_rejected_instead_of_failing_open(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "unknown orchestrator profile"):
                self._make(d, profile="totally-bogus")

    def test_post_build_stages_are_noop_under_benchmark(self):
        # Direct unit check: with every service patched, the benchmark profile's
        # post-build hook returns None and calls NONE of them.
        with tempfile.TemporaryDirectory() as d:
            orch, _ = self._make(d, profile="benchmark")
            with mock.patch("signalos_lib.product.security_gate.run_security_gate") as m_sec, \
                 mock.patch("signalos_lib.product.proof.run_runtime_proof") as m_rt, \
                 mock.patch("signalos_lib.product.proof.run_ux_proof") as m_ux:
                self.assertIsNone(orch._run_post_build_stages())
            m_sec.assert_not_called()
            m_rt.assert_not_called()
            m_ux.assert_not_called()

    # -- production profile invokes the stages ------------------------------

    def _prod_g4(self, orch):
        """Park a production orchestrator at a verified G4 and neutralize the
        advance so the test isolates the post-build stages + G4 sign."""
        orch.state.current_gate = "G4"
        orch._g4_verify = {"ok": True}
        orch._run_gate = lambda g: None  # don't run the real G5 gate on advance

    def test_production_profile_invokes_security_and_proof(self):
        with tempfile.TemporaryDirectory() as d:
            signed: list[str] = []
            orch, events = self._make(d, profile="production", signed=signed)
            self._prod_g4(orch)
            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.security_gate.run_security_gate",
                            return_value={"status": "passed",
                                          "injection_scan": {"issues_found": []}}) as m_sec, \
                 mock.patch("signalos_lib.product.security_gate.write_security_result"), \
                 mock.patch("signalos_lib.product.proof.requires_browser_ux_proof",
                            return_value=True), \
                 mock.patch("signalos_lib.product.proof.run_runtime_proof",
                            return_value={"status": "passed", "port": 4173,
                                          "html_snapshot": "<div id='root'>ok</div>"}) as m_rt, \
                 mock.patch("signalos_lib.product.proof.run_ux_proof",
                            return_value={"status": "passed", "checks": [], "errors": []}) as m_ux, \
                 mock.patch("signalos_lib.product.proof.write_proof_artifacts"):
                res = orch.apply_verdict("approve")
            # Both production services actually ran, and G4 signed + advanced.
            m_sec.assert_called_once()
            m_rt.assert_called_once()
            m_ux.assert_called_once()
            self.assertEqual(res["status"], "advanced")
            self.assertEqual(res["gate"], "G5")
            self.assertIn("G4", signed)
            self.assertTrue(any(e.get("type") == "security_gate" for e in events))
            self.assertTrue(any(e.get("type") == "proof" for e in events))

    def test_production_critical_security_finding_hard_blocks_the_sign(self):
        with tempfile.TemporaryDirectory() as d:
            signed: list[str] = []
            orch, events = self._make(d, profile="production", signed=signed)
            self._prod_g4(orch)
            critical = {"status": "failed", "injection_scan": {"issues_found": [
                {"file": "src/App.tsx", "line": 10,
                 "risk": "XSS risk via dangerouslySetInnerHTML"}]}}
            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.security_gate.run_security_gate",
                            return_value=critical), \
                 mock.patch("signalos_lib.product.security_gate.write_security_result"), \
                 mock.patch("signalos_lib.product.proof.run_runtime_proof") as m_rt:
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "security-blocked")
            self.assertNotIn("G4", signed)                 # sign refused
            self.assertNotIn("G4", orch.state.signed)
            self.assertEqual(orch.state.current_gate, "G4")  # did not advance
            m_rt.assert_not_called()  # blocked BEFORE the (flaky) proof stage
            self.assertTrue(any(e.get("type") == "error" for e in events))

    def test_production_security_warning_fails_open_and_signs(self):
        # A DEGRADED gate ('warning', not a real finding) must NOT block -- that
        # would let a flaky scanner fail a release. It signs; proof still runs.
        with tempfile.TemporaryDirectory() as d:
            signed: list[str] = []
            orch, _ = self._make(d, profile="production", signed=signed)
            self._prod_g4(orch)
            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.security_gate.run_security_gate",
                            return_value={"status": "warning",
                                          "injection_scan": {"issues_found": []}}), \
                 mock.patch("signalos_lib.product.security_gate.write_security_result"), \
                 mock.patch("signalos_lib.product.proof.requires_browser_ux_proof",
                            return_value=False), \
                 mock.patch("signalos_lib.product.proof.run_runtime_proof",
                            return_value={"status": "skipped", "port": None,
                                          "html_snapshot": ""}) as m_rt, \
                 mock.patch("signalos_lib.product.proof.run_ux_proof",
                            return_value={"status": "skipped", "checks": [], "errors": []}), \
                 mock.patch("signalos_lib.product.proof.write_proof_artifacts"):
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "advanced")
            self.assertIn("G4", signed)
            m_rt.assert_called_once()

    # -- CRITICAL benchmark-identical proof ---------------------------------

    def test_benchmark_already_scaffolded_react_identical_and_no_extra_stages(self):
        # Mirrors WS-E's `test_already_scaffolded_react_g4_unchanged_and_product_
        # untouched`, but asserts it explicitly under the (default) BENCHMARK
        # profile AND proves the new profile stages NEVER fire there:
        #   (a) the REAL G4 verified-build outcome is unchanged (still ok);
        #   (b) a full walk signs the SAME gates G0..G5;
        #   (c) product files (src/**, package.json, vite.config.ts) are
        #       byte-identical after the walk; and
        #   (d) run_security_gate / run_runtime_proof / run_ux_proof are NEVER
        #       called under the benchmark profile.
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

            # (a) REAL G4 wall (validation mocked green) verifies a source
            # change attributable to this attempt -- a pre-existing green tree
            # alone is deliberately no longer sufficient.
            real_orch, _ = self._make(d, profile="benchmark")
            del real_orch._verify_g4_build  # drop the stub -> use the real method
            real_orch._prepare_g4_attribution()
            (root / "src" / "components" / "ExpenseList.tsx").write_text(
                "export const ExpenseList = () => <section>Expenses</section>;\n",
                encoding="utf-8")
            with mock.patch("signalos_lib.product.stacks.detect_profile",
                            return_value="react-vite"), \
                 mock.patch("signalos_lib.product.validation.build_validation_plan",
                            return_value={"can_validate_build": True,
                                          "can_validate_tests": True,
                                          "profile": "react-vite"}), \
                 mock.patch("signalos_lib.product.validation.run_validation",
                            return_value={"results": {"build": {"status": "passed"},
                                                      "test": {"status": "passed"}}}):
                self.assertTrue(real_orch._verify_g4_build(_Result())["ok"])

            # (b)-(d) full walk to completion under the benchmark profile, with
            # every profile service patched so we can prove they never fire.
            signed: list[str] = []
            orch, events = self._make(d, profile="benchmark", signed=signed)
            before = self._product_snapshot(root)
            with mock.patch("signalos_lib.product.security_gate.run_security_gate") as m_sec, \
                 mock.patch("signalos_lib.product.proof.run_runtime_proof") as m_rt, \
                 mock.patch("signalos_lib.product.proof.run_ux_proof") as m_ux:
                orch.start()
                res = None
                for _ in range(6):                 # G0..G5 -> complete
                    res = orch.apply_verdict("approve")
            after = self._product_snapshot(root)

            self.assertEqual(res["status"], "complete")
            self.assertEqual(signed, ["G0", "G1", "G2", "G3", "G4", "G5"])
            self.assertEqual(before, after)  # product bytes untouched
            m_sec.assert_not_called()
            m_rt.assert_not_called()
            m_ux.assert_not_called()
            # No benchmark-profile leak of the production evidence events.
            self.assertFalse(any(e.get("type") == "security_gate" for e in events))
            self.assertFalse(any(e.get("type") == "proof" for e in events))


class _Result:
    """Minimal LoopResult stand-in for unit-testing the outcome gate."""
    def __init__(self, status="completed", wrote_no_files=False):
        self.status = status
        self.wrote_no_files = wrote_no_files


def _fresh(orch):
    """Mark the current gate run as having started well in the past so any
    already-seeded artifact counts as freshly-written-this-run."""
    orch._gate_run_started_at = __import__("time").time() - 1000.0


def _seed_g2_plan_contract(root: Path, *, tasks_yaml=True, red_skeleton=True,
                           acceptance=True, plan_md=True, in_parity=True) -> None:
    """Seed the G2 plan-gate contract: Expectation Map + rendered PLAN.md +
    ACCEPTANCE_CRITERIA + machine PLAN.tasks.yaml + a per-task RED test
    skeleton. Flags omit pieces to build the RED (incomplete-contract) cases."""
    exe = root / "core" / "execution"
    strat = root / "core" / "strategy"
    exe.mkdir(parents=True, exist_ok=True)
    strat.mkdir(parents=True, exist_ok=True)
    (strat / "EXPECTATION_MAP.md").write_text(
        "# Expectation Map\n\nWhat the founder expects.\n", encoding="utf-8")
    if acceptance:
        (exe / "ACCEPTANCE_CRITERIA.md").write_text(
            "# Acceptance Criteria\n\n- AC-1: the expense list renders.\n",
            encoding="utf-8")
    title = "Build the expense list"
    if plan_md:
        body = f"# Plan\n\n## {title}\n\nImplement it.\n" if in_parity else \
            "# Plan\n\n## Something unrelated\n"
        (exe / "PLAN.md").write_text(body, encoding="utf-8")
    if tasks_yaml:
        (exe / "PLAN.tasks.yaml").write_text(
            "wave: W1\n"
            "tasks:\n"
            "  - id: T1\n"
            f"    title: {title}\n"
            "    status: pending\n"
            "    tier: T3\n"
            "    files:\n"
            "      - src/ExpenseList.tsx\n"
            "      - src/ExpenseList.test.tsx\n",
            encoding="utf-8")
    if red_skeleton:
        skel = root / "src" / "ExpenseList.test.tsx"
        skel.parent.mkdir(parents=True, exist_ok=True)
        skel.write_text("test('renders', () => { expect(false).toBe(true); });\n",
                        encoding="utf-8")


def _seed_react_product(root: Path) -> None:
    """Seed a react-vite shell + a real product source file so the stack
    profile resolves to react-vite (source dir 'src') and
    _repo_has_real_product_src() detects a genuine built product."""
    (root / "package.json").write_text(json.dumps({
        "dependencies": {"react": "^18.3.1"},
        "devDependencies": {"vite": "^5.4.0", "vitest": "^3.2.0"},
        "scripts": {"build": "tsc && vite build", "test": "vitest run"},
    }) + "\n", encoding="utf-8")
    (root / "src" / "components").mkdir(parents=True, exist_ok=True)
    (root / "src" / "components" / "ExpenseList.tsx").write_text(
        "export const ExpenseList = () => null;\n", encoding="utf-8")


def _bare_orch(root, **kw):
    """A GateOrchestrator on the PRODUCTION sign path (no sign_fn), driven by a
    real _EndAdapter (which honestly stalls). No outcome-gate stub -- these
    tests exercise the outcome gate itself."""
    events = kw.pop("events", None)
    emit = events.append if events is not None else (lambda e: None)
    return GateOrchestrator(
        Path(root), _EndAdapter(), emit,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        prompt="build an expense tracker", **kw)


class TestAgentOutcomeGate(unittest.TestCase):
    """Fix 1 (fail-closed): the walk CONSUMES the agent's structured outcome.
    A gate whose agent refused/errored/stalled/wrote-nothing must NOT open for
    review, and on the production sign path must NOT be approvable."""

    def test_stalled_agent_does_not_open_review(self):
        # RED against the old walk: it discarded the LoopResult and always set
        # 'awaiting-verdict'. The _EndAdapter never calls a tool -> the loop
        # honestly reports stalled_no_tool, so the gate must be 'blocked'.
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch = _bare_orch(d, events=events)
            res = orch.start()
            self.assertNotEqual(orch.state.status, "awaiting-verdict")
            self.assertEqual(orch.state.status, "blocked")
            self.assertEqual(res["gate"], "G0")
            self.assertTrue(any(e.get("type") == "gate_blocked" for e in events))
            # the honest outcome is retained on the state
            self.assertFalse(orch.state.last_outcome["ok"])
            self.assertEqual(orch.state.last_outcome["loop_status"], "stalled_no_tool")

    def test_provider_failure_category_survives_gate_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch = GateOrchestrator(
                Path(d), _ProviderTimeoutAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                prompt="build an expense tracker",
            )
            orch.start()

            self.assertEqual(orch.state.status, "blocked")
            self.assertEqual(
                orch.state.last_outcome["failure_type"], "provider-transport"
            )
            self.assertIn("timed out", orch.state.last_outcome["error"])

    def test_g4_subagent_provider_failure_is_a_typed_loop_outcome(self):
        from signalos_lib.product.subagent_build import ProviderExecutionError

        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            with mock.patch.object(orch, "_scaffold_shell_if_greenfield"), \
                    mock.patch.object(orch, "_prepare_g4_attribution"), \
                    mock.patch(
                        "signalos_lib.product.subagent_build.run_subagent_driven_build",
                        side_effect=ProviderExecutionError(
                            "provider-rate-limit", "provider rate-limiting request"
                        ),
                    ), mock.patch.object(
                        orch, "_verify_g4_build",
                        return_value={"ok": False, "reason": "provider unavailable"},
                    ):
                result = orch._execute_build_gate("G4", "system", [0, 1, 2, 3])

            self.assertEqual(result.status, "error")
            self.assertEqual(result.failure_type, "provider-rate-limit")
            self.assertIn("rate-limiting", result.error)

    def test_g4_sandbox_failure_is_a_typed_loop_outcome(self):
        from signalos_lib.product.subagent_build import ExecutionInfrastructureError

        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            with mock.patch.object(orch, "_scaffold_shell_if_greenfield"), \
                    mock.patch.object(orch, "_prepare_g4_attribution"), \
                    mock.patch(
                        "signalos_lib.product.subagent_build.run_subagent_driven_build",
                        side_effect=ExecutionInfrastructureError(
                            "sandbox-unavailable", "container daemon unavailable"
                        ),
                    ), mock.patch.object(
                        orch, "_verify_g4_build",
                        return_value={"ok": False, "reason": "sandbox unavailable"},
                    ):
                result = orch._execute_build_gate("G4", "system", [0, 1, 2, 3])

            self.assertEqual(result.status, "error")
            self.assertEqual(result.failure_type, "sandbox-unavailable")
            self.assertIn("daemon unavailable", result.error)

    def test_g4_verifier_sandbox_failure_is_a_typed_loop_outcome(self):
        from signalos_lib.product.agent_loop import LoopResult
        from signalos_lib.product.sandbox import SandboxUnavailableError

        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            completed = LoopResult(
                run_id="g4-build",
                status="completed",
                final_text="done",
                tool_calls_made=1,
                messages=[],
            )
            with mock.patch.object(orch, "_scaffold_shell_if_greenfield"), \
                    mock.patch.object(orch, "_prepare_g4_attribution"), \
                    mock.patch(
                        "signalos_lib.product.subagent_build.run_subagent_driven_build",
                        return_value=completed,
                    ), mock.patch.object(
                        orch,
                        "_verify_g4_build",
                        side_effect=SandboxUnavailableError("daemon unavailable"),
                    ):
                result = orch._execute_build_gate("G4", "system", [0, 1, 2, 3])

            self.assertEqual(result.status, "error")
            self.assertEqual(result.failure_type, "sandbox-unavailable")
            self.assertIn("verification containment failed", result.error)

    def test_g4_dependency_broker_failure_blocks_before_model_dispatch(self):
        from signalos_lib.product.dependency_broker import DependencyBrokerError

        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            with mock.patch.object(orch, "_scaffold_shell_if_greenfield"), \
                    mock.patch(
                        "signalos_lib.product.dependency_broker."
                        "materialize_funded_dependencies_from_environment",
                        side_effect=DependencyBrokerError("bundle unavailable"),
                    ), mock.patch(
                        "signalos_lib.product.subagent_build.run_subagent_driven_build"
                    ) as dispatch:
                result = orch._execute_build_gate("G4", "system", [0, 1, 2, 3])

            dispatch.assert_not_called()
            self.assertEqual(result.status, "error")
            self.assertEqual(result.failure_type, "dependency-broker-unavailable")
            self.assertIn("bundle unavailable", result.error)

    def test_g4_scaffolds_then_materializes_before_dispatch_and_verification(self):
        from signalos_lib.product.agent_loop import LoopResult

        order = []
        completed = LoopResult(
            run_id="g4-build",
            status="completed",
            final_text="done",
            tool_calls_made=1,
            messages=[],
        )

        def scaffold():
            order.append("scaffold")

        def materialize(root):
            order.append("materialize")
            return {"schema": "signalos.dependency-receipt.v3", "status": "ready"}

        def checkpoint():
            order.append("checkpoint")

        def dispatch(*args, **kwargs):
            order.append("dispatch")
            return completed

        def verify(result):
            order.append("verify")
            return {"ok": True}

        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            with mock.patch.object(
                orch, "_scaffold_shell_if_greenfield", side_effect=scaffold
            ), mock.patch(
                "signalos_lib.product.dependency_broker."
                "materialize_funded_dependencies_from_environment",
                side_effect=materialize,
            ), mock.patch.object(
                orch, "_prepare_g4_attribution", side_effect=checkpoint
            ), mock.patch(
                "signalos_lib.product.subagent_build.run_subagent_driven_build",
                side_effect=dispatch,
            ), mock.patch.object(
                orch, "_verify_g4_build", side_effect=verify
            ):
                result = orch._execute_build_gate("G4", "system", [0, 1, 2, 3])

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            order,
            ["scaffold", "materialize", "checkpoint", "dispatch", "verify"],
        )

    def test_stalled_agent_not_approvable_even_with_artifacts_present(self):
        # STRONGEST fail-open proof: seed ALL FOUR G0 artifacts so the old
        # _default_sign would happily sign + advance a gate whose agent actually
        # stalled. The new outcome gate refuses to approve it.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g0_artifacts(root)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            orch = _bare_orch(d)
            orch.start()
            res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "not-reviewable")
            self.assertNotIn("G0", orch.state.signed)
            self.assertEqual(orch.state.current_gate, "G0")

    def test_completed_agent_with_fresh_artifacts_opens_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g0_artifacts(root)
            orch = _bare_orch(d)
            _fresh(orch)
            outcome = orch._gate_review_ready("G0", _Result("completed", False))
            self.assertTrue(outcome["ok"], outcome)

    def test_review_ready_rejects_each_bad_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g0_artifacts(root)
            orch = _bare_orch(d)
            _fresh(orch)
            for bad in ("error", "cancelled", "budget_exhausted",
                        "stalled_no_tool", "max_tokens", "text_only"):
                self.assertFalse(
                    orch._gate_review_ready("G0", _Result(bad, False))["ok"], bad)
            # completed but wrote nothing this run -> narration only, not ready
            self.assertFalse(
                orch._gate_review_ready("G0", _Result("completed", True))["ok"])

    def test_review_ready_requires_current_run_artifacts(self):
        # artifacts on disk but written BEFORE this run (stale) -> not reviewable
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g0_artifacts(root)
            orch = _bare_orch(d)
            orch._gate_run_started_at = __import__("time").time() + 1000.0  # future
            outcome = orch._gate_review_ready("G0", _Result("completed", False))
            self.assertFalse(outcome["ok"])
            self.assertIn("stale", outcome["reason"])

    def test_g4_outcome_gate_defers_to_build_verification(self):
        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            orch._g4_verify = {"ok": False, "reason": "no real product source"}
            self.assertFalse(orch._gate_review_ready("G4", None)["ok"])
            orch._g4_verify = {"ok": True}
            self.assertTrue(orch._gate_review_ready("G4", None)["ok"])


class TestG0AllArtifactsFailClosed(unittest.TestCase):
    """Fix 2: _default_sign must require EVERY manifest-required artifact, not
    just >=1. A gate with 3 of its 4 required artifacts cannot be signed."""

    def test_default_sign_refuses_three_of_four_g0_artifacts(self):
        # RED against old _default_sign: it signed once ANY artifact existed.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gov = root / "core" / "governance" / "Governance"
            gov.mkdir(parents=True, exist_ok=True)
            # Only 3 of the 4 required G0 artifacts (omit PERMANENTLY_T3).
            for name in ("SOUL-DOCUMENT.md", "CONSTITUTION.md", "SURFACE_INVENTORY.md"):
                (gov / name).write_text(f"# {name}\n\nreal content\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                go_mod._default_sign(root, "G0", "signer", "PE", "APPROVED", "")
            self.assertIn("PERMANENTLY_T3", str(ctx.exception))

    def test_default_sign_allows_all_four_g0_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g0_artifacts(root)
            signed = go_mod._default_sign(root, "G0", "signer", "PE", "APPROVED", "")
            self.assertTrue(signed)


class TestG2PlanContract(unittest.TestCase):
    """Fix 3: G2 (the Plan gate) requires an EXECUTABLE + TESTABLE plan --
    PLAN.tasks.yaml + rendered PLAN.md parity + ACCEPTANCE_CRITERIA + per-task
    RED skeletons -- not just the Expectation Map."""

    def _orch(self, root):
        return _bare_orch(root)

    def test_g2_not_reviewable_with_only_expectation_map(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "core" / "strategy").mkdir(parents=True, exist_ok=True)
            (root / "core" / "strategy" / "EXPECTATION_MAP.md").write_text(
                "# Expectation Map\n\nexpectations\n", encoding="utf-8")
            orch = self._orch(root)
            _fresh(orch)
            outcome = orch._gate_review_ready("G2", _Result("completed", False))
            self.assertFalse(outcome["ok"])
            self.assertIn("PLAN.tasks.yaml", outcome["reason"])

    def test_g2_reviewable_with_full_plan_contract(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g2_plan_contract(root)
            orch = self._orch(root)
            _fresh(orch)
            outcome = orch._gate_review_ready("G2", _Result("completed", False))
            self.assertTrue(outcome["ok"], outcome)

    def test_g2_not_reviewable_without_red_skeleton(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g2_plan_contract(root, red_skeleton=False)
            orch = self._orch(root)
            _fresh(orch)
            problems = orch._validate_g2_plan_contract()
            self.assertTrue(any("RED test skeleton" in p for p in problems), problems)

    def test_g2_not_reviewable_without_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g2_plan_contract(root, acceptance=False)
            orch = self._orch(root)
            problems = orch._validate_g2_plan_contract()
            self.assertTrue(any("ACCEPTANCE_CRITERIA" in p for p in problems), problems)

    def test_g2_flags_plan_md_out_of_parity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_g2_plan_contract(root, in_parity=False)
            orch = self._orch(root)
            problems = orch._validate_g2_plan_contract()
            self.assertTrue(any("parity" in p for p in problems), problems)


class TestG5ReleaseReadiness(unittest.TestCase):
    """Fix 4: readiness at G5 reflects a real release verification, never just
    'no waivers'. No built product / unresolved condition / waiver -> not ready
    (the delivery still COMPLETES, but is honestly reported not-ready)."""

    @staticmethod
    def _passing_browser_runtime() -> dict:
        return {
            "status": "passed",
            "stack": "react-vite",
            "ux_required": True,
            "ux_status": "passed",
            "ux_executed": True,
            "ux_schema_version": "signalos.ux-browser-proof.v1",
            "ok": True,
        }

    def test_not_ready_without_built_product(self):
        with tempfile.TemporaryDirectory() as d:
            orch = _bare_orch(d)
            rel = orch._verify_g5_release()
            self.assertFalse(rel["ok"])
            self.assertTrue(any("built product" in r for r in rel["reasons"]))

    def test_ready_with_built_product_and_no_waivers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_react_product(root)
            orch = _bare_orch(root)
            self.assertTrue(orch._verify_g5_release()["ok"])

    def test_waiver_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_react_product(root)
            orch = _bare_orch(root)
            orch.state.waived.append("G1")
            rel = orch._verify_g5_release()
            self.assertFalse(rel["ok"])
            self.assertTrue(any("waived" in r for r in rel["reasons"]))

    def test_production_requires_runtime_proof(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_react_product(root)
            orch = _bare_orch(root, profile="production")
            orch.state.release_evidence["security_gate"] = {"status": "passed"}
            orch._last_runtime_ok = False
            self.assertFalse(orch._verify_g5_release()["ok"])
            orch.state.release_evidence["runtime_proof"] = (
                self._passing_browser_runtime()
            )
            orch._last_runtime_ok = True
            self.assertTrue(orch._verify_g5_release()["ok"])

    def test_production_browser_ux_failed_skipped_unmeasurable_or_missing_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_react_product(root)
            orch = _bare_orch(root, profile="production")
            orch.state.release_evidence["security_gate"] = {"status": "passed"}
            for ux_status in ("failed", "skipped", "unmeasurable", None):
                with self.subTest(ux_status=ux_status):
                    evidence = self._passing_browser_runtime()
                    if ux_status is None:
                        evidence.pop("ux_status")
                    else:
                        evidence["ux_status"] = ux_status
                    evidence["ux_executed"] = ux_status == "passed"
                    evidence["ok"] = ux_status == "passed"
                    orch.state.release_evidence["runtime_proof"] = evidence
                    orch._last_runtime_ok = evidence["ok"]
                    result = orch._verify_g5_release()
                    self.assertFalse(result["ok"], result)
                    self.assertTrue(any(
                        "browser UX proof" in reason
                        for reason in result["reasons"]
                    ), result)

    def test_production_requires_passing_security_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_react_product(root)
            orch = _bare_orch(root, profile="production")
            orch.state.release_evidence["runtime_proof"] = (
                self._passing_browser_runtime()
            )
            orch._last_runtime_ok = True

            missing = orch._verify_g5_release()
            self.assertFalse(missing["ok"])
            self.assertIn(
                "production profile: security gate did not pass",
                missing["reasons"],
            )

            orch.state.release_evidence["security_gate"] = {"status": "warning"}
            warning = orch._verify_g5_release()
            self.assertFalse(warning["ok"])
            self.assertIn(
                "production profile: security gate did not pass",
                warning["reasons"],
            )

            orch.state.release_evidence["security_gate"] = {"status": "passed"}
            self.assertTrue(orch._verify_g5_release()["ok"])


class TestWaiverAndConditions(unittest.TestCase):
    """Fix 5: waivers require a written reason; approve-with-conditions records
    an UNRESOLVED condition that blocks readiness -- never completes silently."""

    def test_blank_waiver_reason_is_refused(self):
        # RED against old: waive advanced the gate with 'no reason given'.
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            res = orch.apply_verdict("waive", "   ")
            self.assertEqual(res["status"], "waive-needs-reason")
            self.assertEqual(orch.state.current_gate, "G0")   # did not advance
            self.assertNotIn("G0", orch.state.waived)

    def test_blank_condition_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            res = orch.apply_verdict("approve-with-conditions", "")
            self.assertEqual(res["status"], "conditions-need-text")
            self.assertNotIn("G0", orch.state.signed)

    def test_approve_with_conditions_blocks_readiness_to_the_end(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            events, signed = [], []
            orch = _orch(root, events, signed, release_ready=False)
            # Isolate the blocker: pretend a real product was built, so ONLY the
            # unresolved condition can keep the delivery from being ready.
            orch._repo_has_real_product_src = lambda: True
            orch.start()
            # approve G0 WITH a condition, then approve the rest through to G5
            r0 = orch.apply_verdict("approve-with-conditions", "add rate limiting before ship")
            self.assertEqual(r0["status"], "advanced")
            self.assertIn("G0", orch.state.conditions)
            res = None
            for _ in range(5):
                res = orch.apply_verdict("approve")
            self.assertEqual(res["status"], "release-not-ready")
            self.assertFalse(res["ready"])
            self.assertIn("G0", orch.state.conditions)
            self.assertNotIn("G5", orch.state.signed)


class TestPersistBeforeDispatch(unittest.TestCase):
    """Fix 6: the freshly-signed state is persisted DURABLY before the next
    gate is dispatched, and a failed persist surfaces (is not swallowed)."""

    def _read_state(self, root, run_id):
        sf = Path(root) / ".signalos" / "agent-runs" / run_id / "delivery.json"
        return json.loads(sf.read_text(encoding="utf-8"))

    def test_signature_persisted_before_next_gate_dispatch(self):
        # RED against old: it dispatched the next gate BEFORE persisting the
        # signature, so a crash mid-next-gate left delivery.json showing the
        # PRIOR gate unsigned. Simulate the crash by making the next _run_gate
        # raise, then prove the signed state was already on disk.
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()                                   # G0 opened
            run_id = orch.state.run_id

            def _boom(gate):
                raise RuntimeError(f"crash while dispatching {gate}")
            orch._run_gate = _boom
            with self.assertRaises(RuntimeError):
                orch.apply_verdict("approve")              # sign G0 -> dispatch G1 (crash)

            data = self._read_state(d, run_id)
            self.assertIn("G0", data["signed"])            # signature durable
            self.assertEqual(data["current_gate"], "G1")   # advanced pre-dispatch

    def test_persist_failure_surfaces(self):
        # RED against old: _persist swallowed OSError.
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            blocker = Path(d) / "blocker"
            blocker.write_text("i am a file, not a dir\n", encoding="utf-8")
            orch._state_dir = lambda: blocker / "sub"       # mkdir under a file -> OSError
            with self.assertRaises(OSError):
                orch._persist()


def _spawn_dead_pid() -> int:
    """A pid that is guaranteed DEAD: spawn a trivial child, wait for it to exit
    (code 0, never STILL_ACTIVE/259), and return its now-reaped pid. Used to
    prove the liveness check reclaims a same-host, dead-pid lock."""
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    p.wait()
    return p.pid


class TestSingleActiveDeliveryLock(unittest.TestCase):
    """Workspace-global delivery lock protecting shared product/Git bytes."""

    def _orch(self, root, *, project_id="default", run_id=None, events=None):
        ev = events if events is not None else []
        orch = GateOrchestrator(
            Path(root), _EndAdapter(), ev.append,
            enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
            sign_fn=lambda *a, **k: ["x"], prompt="build task management",
            project_id=project_id, run_id=run_id,
        )
        # Simulate a SUCCESSFUL gate agent so start() opens the gate for review
        # (awaiting-verdict) instead of blocking on the stalled _EndAdapter --
        # this isolates the LOCK behaviour from the agent-outcome gate. (Same
        # spirit as the module-level _orch / TestCloseoutConvergence stubs.)
        orch._verify_g4_build = lambda *a, **k: {"ok": True}
        orch._execute_build_gate = lambda *a, **k: setattr(
            orch, "_g4_verify", {"ok": True})
        orch._gate_review_ready = lambda *a, **k: {"ok": True}
        orch._verify_g5_release = lambda **k: {"ok": True, "reasons": []}
        return orch, ev

    @staticmethod
    def _lock_path(root, project_id="default"):
        return Path(root) / ".signalos" / "locks" / "delivery.lock"

    # -- (a) two deliveries on the SAME project -> the second is blocked ----

    def test_second_delivery_same_project_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            a, _ = self._orch(d, run_id="run-A")
            self.assertEqual(a.start()["status"], "awaiting-verdict")

            b, ev_b = self._orch(d, run_id="run-B")  # same repo + project
            res_b = b.start()
            self.assertEqual(res_b["status"], "blocked")
            self.assertIn("already active in this workspace", res_b["reason"])
            self.assertIn("run-A", res_b["reason"])  # names the holder
            self.assertTrue(any(e.get("type") == "delivery_blocked" for e in ev_b))
            # blocked BEFORE running its own gate (no gate event emitted)
            self.assertFalse(any(e.get("type") == "gate" for e in ev_b))
            # the first delivery's lock is intact and still its own
            info = json.loads(self._lock_path(d).read_text(encoding="utf-8"))
            self.assertEqual(info["run_id"], "run-A")

    # -- (b) virtual projects still share product/Git bytes -----------------

    def test_different_projects_are_serialized(self):
        with tempfile.TemporaryDirectory() as d:
            a, ev_a = self._orch(d, project_id="alpha", run_id="run-alpha")
            b, ev_b = self._orch(d, project_id="beta", run_id="run-beta")
            self.assertEqual(a.start()["status"], "awaiting-verdict")
            self.assertEqual(b.start()["status"], "blocked")
            self.assertTrue(any(e.get("type") == "delivery_blocked" for e in ev_b))
            self.assertEqual(
                json.loads(self._lock_path(d).read_text())["run_id"],
                "run-alpha")

    # -- (c) a STALE lock is reclaimed, not blocked ------------------------

    def _seed_lock(self, root, *, run_id, pid, acquired_at, project_id="default",
                   host=None):
        lp = self._lock_path(root, project_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps({
            "run_id": run_id, "pid": pid,
            "host": host if host is not None else go_mod._hostname(),
            "acquired_at": acquired_at}), encoding="utf-8")
        return lp

    def test_stale_lock_past_ttl_is_reclaimed(self):
        # LIVE pid (this very process) but acquired 7h ago -> past the 6h TTL
        # -> stale -> reclaimed (proves TTL crash-safety without needing a dead
        # pid).
        with tempfile.TemporaryDirectory() as d:
            old = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            self._seed_lock(d, run_id="run-old", pid=os.getpid(), acquired_at=old,
                            host="otherhost")
            orch, ev = self._orch(d, run_id="run-new")
            self.assertEqual(orch.start()["status"], "awaiting-verdict")
            self.assertFalse(any(e.get("type") == "delivery_blocked" for e in ev))
            self.assertEqual(
                json.loads(self._lock_path(d).read_text())["run_id"], "run-new")

    def test_stale_lock_dead_pid_is_reclaimed(self):
        # Recent timestamp but a same-host DEAD pid -> stale -> reclaimed.
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._seed_lock(d, run_id="run-old", pid=_spawn_dead_pid(),
                            acquired_at=now)
            orch, ev = self._orch(d, run_id="run-new")
            self.assertEqual(orch.start()["status"], "awaiting-verdict")
            self.assertFalse(any(e.get("type") == "delivery_blocked" for e in ev))
            self.assertEqual(
                json.loads(self._lock_path(d).read_text())["run_id"], "run-new")

    # -- (d) ownership is per instance, not merely run/pid/host ------------

    def test_second_instance_same_process_and_run_id_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            a, _ = self._orch(d, run_id="run-X")
            a.start()  # acquires the lock for run-X (live pid, fresh timestamp)

            # The acquiring instance may safely re-enter its own lock.
            self.assertIsNone(a._acquire_delivery_lock())
            owner = json.loads(self._lock_path(d).read_text(encoding="utf-8"))
            self.assertEqual(owner["owner_token"], a._delivery_lock_owner_token)

            # A reconstructed object shares run_id, pid, and host, but has a
            # distinct owner token and therefore remains a real contender.
            b, ev_b = self._orch(d, run_id="run-X")
            self.assertNotEqual(
                b._delivery_lock_owner_token,
                a._delivery_lock_owner_token,
            )
            blocked = b.start()
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(any(e.get("type") == "delivery_blocked" for e in ev_b))

            # The contender cannot release the live owner's lock; the owner can.
            b._release_delivery_lock()
            self.assertTrue(self._lock_path(d).is_file())
            a._release_delivery_lock()
            self.assertFalse(self._lock_path(d).exists())

    # -- (e) a terminal status releases the lock --------------------------

    def test_terminal_complete_releases_lock(self):
        with tempfile.TemporaryDirectory() as d:
            a, _ = self._orch(d, run_id="run-1")
            a.start()
            self.assertTrue(self._lock_path(d).is_file())  # held during delivery
            res = None
            for _ in range(6):                 # G0..G5 -> complete
                res = a.apply_verdict("approve")
            self.assertEqual(res["status"], "complete")
            self.assertFalse(self._lock_path(d).is_file(),
                             "a completed delivery must release its lock")
            # a SUBSEQUENT delivery on the SAME project now starts fine
            b, ev_b = self._orch(d, run_id="run-2")
            self.assertEqual(b.start()["status"], "awaiting-verdict")
            self.assertFalse(any(e.get("type") == "delivery_blocked" for e in ev_b))

    def test_terminal_complete_waived_releases_lock(self):
        # complete-waived is the terminal status when the FINAL gate (G5) is
        # itself waived, advancing without a signature.
        with tempfile.TemporaryDirectory() as d:
            a, _ = self._orch(d, run_id="run-1")
            a.start()
            for _ in range(5):                          # approve G0..G4 -> at G5
                a.apply_verdict("approve")
            res = a.apply_verdict("waive", "ship without the release sign-off")
            self.assertEqual(res["status"], "complete-waived")
            self.assertFalse(self._lock_path(d).is_file(),
                             "a waived-to-complete delivery must release its lock")

    def test_release_only_touches_our_own_lock(self):
        # A terminal release must NEVER delete another delivery's lock.
        with tempfile.TemporaryDirectory() as d:
            self._seed_lock(
                d, run_id="someone-else", pid=os.getpid(),
                acquired_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            orch, _ = self._orch(d, run_id="run-mine")
            orch._release_delivery_lock()  # our run_id != the lock's -> no-op
            self.assertTrue(self._lock_path(d).is_file())
            self.assertEqual(
                json.loads(self._lock_path(d).read_text())["run_id"], "someone-else")

    # -- pid-liveness cross-platform contract -----------------------------

    def test_pid_liveness_detects_dead_and_live(self):
        self.assertTrue(go_mod._pid_is_alive(os.getpid()))  # this process is live
        self.assertFalse(go_mod._pid_is_alive(_spawn_dead_pid()))  # reaped -> dead
        self.assertTrue(go_mod._pid_is_alive(0))            # non-positive -> assume live
        self.assertTrue(go_mod._pid_is_alive("nope"))       # unparseable -> assume live

    def test_lock_liveness_predicate(self):
        # Same-host live PIDs never expire; remote locks use TTL.
        now = datetime.now(timezone.utc)
        fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (now - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with tempfile.TemporaryDirectory() as d:
            orch, _ = self._orch(d, run_id="r")
            self.assertFalse(orch._delivery_lock_is_live(
                {"pid": _spawn_dead_pid(), "host": go_mod._hostname(),
                 "acquired_at": fresh}))
            self.assertTrue(orch._delivery_lock_is_live(
                {"pid": os.getpid(), "host": go_mod._hostname(),
                 "acquired_at": fresh}))
            self.assertTrue(orch._delivery_lock_is_live(
                {"pid": os.getpid(), "host": go_mod._hostname(),
                 "acquired_at": old}))
            self.assertFalse(orch._delivery_lock_is_live(
                {"pid": os.getpid(), "host": "remote", "acquired_at": old}))
            self.assertFalse(orch._delivery_lock_is_live(
                {"pid": os.getpid(), "host": "remote"}))


if __name__ == "__main__":
    unittest.main()
