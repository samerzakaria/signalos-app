"""Gate-reopen mechanism tests (#4 + #5).

Part 1 - GateOrchestrator.reopen_gate: cascade invalidation of later signed/
waived gates, role authorization (same set sign_gate enforces), reopen budget
(SIGNALOS_GATE_REOPEN_BUDGET), feedback threading into the re-run message,
audit events replay understands (audit_replay reverse markers), persistence
via resume_delivery (including legacy delivery.json without the new fields).

Part 2 - the agent:reopen-gate IPC command (active + persisted deliveries).

Part 3 - scope-drift vs LATER signed gates: detect_scope_drift flags clear
conflicts with a signed G2 plan / G3 design as {"conflicting_gate", "reopen-
gate"} and resolve_scope_drift gains option (e) "reopen".

Deterministic: end-turn adapter stubs + recording sign_fn doubles, matching
test_product_gate_orchestrator.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.audit_replay import _REVERSE_MARKERS, load_audit_trail, replay_state
from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product.gate_orchestrator import (
    GateOrchestrator,
    resume_delivery,
)
from signalos_lib.wave_engine import WaveEngine, WaveState, detect_scope_drift


class _EndAdapter:
    """Adapter stub: every turn ends immediately (no tools)."""
    supports_tool_calls = True

    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(content="(gate work done)", tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


class _RecordingAdapter:
    """Captures every user-role message so tests can assert the exact
    rework message the gate agent receives after a reopen."""
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


def _orch(root, events, signed, *, adapter=None, **kw):
    def fake_sign(repo_root, gate, signer, role, verdict, conditions):
        signed.append((gate, role, verdict))
        return [f"{gate}.md"]
    return GateOrchestrator(
        Path(root), adapter or _EndAdapter(), events.append,
        enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
        sign_fn=fake_sign, prompt="build task management",
        **kw,
    )


def _approve_n(orch, n):
    for _ in range(n):
        orch.apply_verdict("approve")


def _audit_rows(root):
    audit = Path(root) / ".signalos" / "AUDIT_TRAIL.jsonl"
    if not audit.is_file():
        return []
    return [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()
            if l.strip()]


class TestReopenCascade(unittest.TestCase):
    def test_reopen_signed_g3_invalidates_g4_g5_with_audit(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            _approve_n(orch, 6)                       # all of G0..G5 signed
            self.assertEqual(orch.state.status, "complete")
            events.clear()

            res = orch.reopen_gate("G3", "the checkout flow is wrong",
                                   name="Sam", role="PE")
            self.assertEqual(res["status"], "reopened")
            self.assertEqual(res["gate"], "G3")
            self.assertEqual(res["invalidated"], ["G4", "G5"])
            self.assertEqual(orch.state.signed, ["G0", "G1", "G2"])
            self.assertEqual(orch.state.current_gate, "G3")
            self.assertEqual(orch.state.status, "reopened")
            self.assertEqual(orch.state.reopens, {"G3": 1})
            # invalidated record: target not cascade, later gates cascade
            inv = {e["gate"]: e for e in orch.state.invalidated}
            self.assertFalse(inv["G3"]["cascade"])
            self.assertTrue(inv["G4"]["cascade"])
            self.assertTrue(inv["G5"]["cascade"])
            self.assertEqual(inv["G4"]["by"], "Sam")
            self.assertEqual(inv["G4"]["reason"], "the checkout flow is wrong")
            # audit: one reversal row per gate, using replay-legible markers
            rows = _audit_rows(d)
            reopen_rows = {r["gate"]: r for r in rows if r.get("kind") in
                           ("reopen", "invalidate")}
            self.assertEqual(set(reopen_rows), {"G3", "G4", "G5"})
            self.assertEqual(reopen_rows["G3"]["kind"], "reopen")
            self.assertEqual(reopen_rows["G4"]["kind"], "invalidate")
            for r in reopen_rows.values():
                self.assertTrue(
                    any(m in r["action"] for m in _REVERSE_MARKERS),
                    f"audit action {r['action']!r} lacks a replay reverse marker")
            # feedback threading also audited (gate_review REOPEN event)
            reviews = [r for r in rows if r.get("event") == "gate_review"
                       and r.get("gate_id") == "G3"]
            self.assertTrue(reviews)
            self.assertEqual(reviews[-1]["verdict"], "REOPEN")
            # system + structured events emitted for the UI
            reopened = [e for e in events if e.get("type") == "gate_reopened"]
            self.assertTrue(reopened)
            self.assertEqual(reopened[0]["invalidated"], ["G4", "G5"])
            self.assertTrue(any(e.get("type") == "system" and "G3 reopened"
                                in e.get("text", "") for e in events))

    def test_audit_replay_understands_reopen_direction(self):
        """A sign row followed by the reopen's reversal rows must fold back
        to unsigned in audit_replay.replay_state."""
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            _approve_n(orch, 5)                       # G0..G4 signed, at G5
            # seed replay-visible sign rows (the fake sign_fn writes none)
            audit = Path(d) / ".signalos" / "AUDIT_TRAIL.jsonl"
            audit.parent.mkdir(parents=True, exist_ok=True)
            with audit.open("a", encoding="utf-8") as f:
                for g in ("G3", "G4"):
                    f.write(json.dumps({"action": "sign", "gate": g}) + "\n")
            orch.reopen_gate("G3", "wrong flow", role="PE")
            entries = load_audit_trail(d)
            state = replay_state(entries, len(entries) - 1)
            self.assertFalse(state["gates"]["G3"]["signed"])
            self.assertFalse(state["gates"]["G4"]["signed"])

    def test_reopen_unwaives_later_waived_gates(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            orch.apply_verdict("approve")             # G0 signed -> G1
            orch.apply_verdict("approve")             # G1 signed -> G2
            orch.apply_verdict("waive", "n/a")        # G2 waived -> G3
            orch.apply_verdict("approve")             # G3 signed -> G4
            self.assertEqual(orch.state.waived, ["G2"])

            res = orch.reopen_gate("G1", "belief changed", role="PO")
            self.assertEqual(res["status"], "reopened")
            self.assertEqual(res["invalidated"], ["G2", "G3"])
            self.assertEqual(orch.state.signed, ["G0"])
            self.assertEqual(orch.state.waived, [])
            unwaive = [e for e in _audit_rows(d) if e.get("kind") == "unwaive"]
            self.assertEqual(len(unwaive), 1)
            self.assertEqual(unwaive[0]["gate"], "G2")


class TestReopenRefusals(unittest.TestCase):
    def test_unauthorized_role_refused(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            _approve_n(orch, 6)                       # G5 signed (QA territory)
            res = orch.reopen_gate("G5", "not ready", role="PO")
            self.assertEqual(res["status"], "role-not-authorized")
            self.assertIn("G5", orch.state.signed)    # no state change
            self.assertEqual(orch.state.reopens, {})
            self.assertEqual(orch.state.invalidated, [])

    def test_not_signed_gate_refused(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()                              # nothing signed yet
            res = orch.reopen_gate("G2", "why not", role="PO")
            self.assertEqual(res["status"], "not-signed")

    def test_unknown_gate_refused(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            self.assertEqual(orch.reopen_gate("G9", "x")["status"],
                             "unknown-gate")

    def test_mid_gate_run_refused(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            orch.apply_verdict("approve")
            orch.state.status = "active"              # simulate in-flight run
            res = orch.reopen_gate("G0", "change of heart", role="PE")
            self.assertEqual(res["status"], "delivery-busy")
            self.assertIn("G0", orch.state.signed)

    def test_reopen_budget_hits_max_reopens(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed, max_reopens=1)
            orch.start()
            orch.apply_verdict("approve")             # G0 signed
            self.assertEqual(
                orch.reopen_gate("G0", "first", role="PE")["status"], "reopened")
            orch.apply_verdict("approve")             # re-sign G0
            res = orch.reopen_gate("G0", "second", role="PE")
            self.assertEqual(res["status"], "max-reopens")
            self.assertIn("G0", orch.state.signed)    # no state change
            self.assertEqual(orch.state.reopens, {"G0": 1})
            refused = [r for r in _audit_rows(d)
                       if r.get("kind") == "reopen-refused"]
            self.assertEqual(len(refused), 1)
            # refusal row must NOT read as a reversal in replay: no `gate` key
            self.assertNotIn("gate", refused[0])
            self.assertEqual(refused[0]["gate_id"], "G0")

    def test_budget_env_override_and_default(self):
        from signalos_lib.product.budgets import (
            DEFAULT_GATE_REOPEN_BUDGET,
            resolve_gate_reopen_budget,
        )
        self.assertEqual(DEFAULT_GATE_REOPEN_BUDGET, 3)
        self.assertIsNone(os.environ.get("SIGNALOS_GATE_REOPEN_BUDGET"))
        self.assertEqual(resolve_gate_reopen_budget(None), 3)
        os.environ["SIGNALOS_GATE_REOPEN_BUDGET"] = "5"
        try:
            self.assertEqual(resolve_gate_reopen_budget(None), 5)
            with tempfile.TemporaryDirectory() as d:
                events, signed = [], []
                self.assertEqual(_orch(d, events, signed).max_reopens, 5)
        finally:
            os.environ.pop("SIGNALOS_GATE_REOPEN_BUDGET", None)


class TestReopenFeedbackThreading(unittest.TestCase):
    def test_reopened_gate_message_contains_reason(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            adapter = _RecordingAdapter()
            orch = _orch(d, events, signed, adapter=adapter)
            orch.start()
            _approve_n(orch, 4)                       # G0..G3 signed, at G4
            orch.reopen_gate("G3", "make the nav horizontal", role="PE")
            msg = orch._gate_message("G3")
            self.assertIn("make the nav horizontal", msg)
            self.assertIn("Reopen 1", msg)
            # and the actual re-run (request-changes path) carries it too
            adapter.user_messages.clear()
            res = orch.apply_verdict("request-changes", "also fix the footer")
            self.assertEqual(res["status"], "reworked")
            joined = "\n---\n".join(adapter.user_messages)
            self.assertIn("make the nav horizontal", joined)
            self.assertIn("also fix the footer", joined)


class TestReopenPersistence(unittest.TestCase):
    def test_resume_restores_reopens_and_invalidated(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            _approve_n(orch, 4)
            orch.reopen_gate("G2", "plan changed", role="PO")

            loaded = resume_delivery(
                Path(d), orch.state.run_id, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: [],
            )
            self.assertEqual(loaded.state.reopens, {"G2": 1})
            self.assertEqual([e["gate"] for e in loaded.state.invalidated],
                             ["G2", "G3"])
            self.assertEqual(loaded.state.current_gate, "G2")
            self.assertEqual(loaded.state.status, "reopened")
            self.assertIn("plan changed", loaded._gate_message("G2"))

    def test_legacy_delivery_json_without_new_fields_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            events, signed = [], []
            orch = _orch(d, events, signed)
            orch.start()
            orch.apply_verdict("approve")
            sf = (Path(d) / ".signalos" / "agent-runs" / orch.state.run_id
                  / "delivery.json")
            data = json.loads(sf.read_text(encoding="utf-8"))
            del data["reopens"]
            del data["invalidated"]
            sf.write_text(json.dumps(data), encoding="utf-8")

            legacy = resume_delivery(
                Path(d), orch.state.run_id, _EndAdapter(), events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                sign_fn=lambda *a, **k: [],
            )
            self.assertEqual(legacy.state.reopens, {})
            self.assertEqual(legacy.state.invalidated, [])
            # and a reopen still works on the resumed legacy state
            res = legacy.reopen_gate("G0", "revisit purpose", role="PE")
            self.assertEqual(res["status"], "reopened")


class TestReopenIPC(unittest.TestCase):
    """agent:reopen-gate wiring - active delivery, persisted delivery,
    and clear errors. Same seams/pattern as TestDeliveryIPC."""

    def _seams(self, srv, signed):
        srv._AGENT_ADAPTER_FACTORY = lambda model: _EndAdapter()
        srv._AGENT_ENFORCEMENT_FACTORY = (
            lambda: StaticEnforcementProvider(trust_tier="T3"))
        srv._DELIVERY_SIGN_FN = (
            lambda root, gate, signer, role, verdict, conditions:
            signed.append((gate, verdict)) or [f"{gate}.md"])

    def _clear_seams(self, srv):
        srv._AGENT_ADAPTER_FACTORY = None
        srv._AGENT_ENFORCEMENT_FACTORY = None
        srv._DELIVERY_SIGN_FN = None
        srv._ACTIVE_DELIVERIES.clear()

    def test_reopen_on_active_delivery_then_verdict_reruns(self):
        import io, contextlib
        import signalos_ipc_server as srv
        signed = []
        self._seams(srv, signed)
        try:
            with tempfile.TemporaryDirectory() as d:
                cwd = os.getcwd()
                os.chdir(d)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        srv.handle({"command": "agent:deliver", "id": "1",
                                    "args": [json.dumps({
                                        "prompt": "build CRM", "run_id": "re-1",
                                        "provider": "openai", "model": "gpt-test"})]})
                        for _ in range(3):            # sign G0..G2, at G3
                            srv.handle({"command": "agent:verdict", "id": "v",
                                        "args": [json.dumps({
                                            "run_id": "re-1",
                                            "verdict": "approve"})]})
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r = srv.handle({"command": "agent:reopen-gate", "id": "2",
                                        "args": [json.dumps({
                                            "run_id": "re-1", "gate": "G1",
                                            "reason": "belief shifted",
                                            "role": "PO"})]})
                    self.assertTrue(r["ok"], r)
                    self.assertEqual(r["data"]["status"], "reopened")
                    self.assertEqual(r["data"]["invalidated"], ["G2"])
                    orch = srv._ACTIVE_DELIVERIES["re-1"]
                    self.assertEqual(orch.state.current_gate, "G1")
                    events = [json.loads(l) for l in buf.getvalue().splitlines()
                              if l.strip().startswith("{")]
                    self.assertTrue(any(e.get("type") == "gate_reopened"
                                        for e in events))
                    # the walk continues exactly like apply_verdict's flow
                    with contextlib.redirect_stdout(io.StringIO()):
                        r2 = srv.handle({"command": "agent:verdict", "id": "3",
                                         "args": [json.dumps({
                                             "run_id": "re-1",
                                             "verdict": "request-changes",
                                             "feedback": "redo it"})]})
                    self.assertTrue(r2["ok"], r2)
                    self.assertEqual(r2["data"]["status"], "reworked")
                    self.assertEqual(r2["data"]["gate"], "G1")
                finally:
                    os.chdir(cwd)
        finally:
            self._clear_seams(srv)

    def test_reopen_persisted_delivery_needs_no_model(self):
        import io, contextlib
        import signalos_ipc_server as srv
        signed = []
        self._seams(srv, signed)
        try:
            with tempfile.TemporaryDirectory() as d:
                cwd = os.getcwd()
                os.chdir(d)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        srv.handle({"command": "agent:deliver", "id": "1",
                                    "args": [json.dumps({
                                        "prompt": "build CRM", "run_id": "re-2",
                                        "provider": "openai", "model": "gpt-test"})]})
                        srv.handle({"command": "agent:verdict", "id": "v",
                                    "args": [json.dumps({"run_id": "re-2",
                                                         "verdict": "approve"})]})
                    srv._ACTIVE_DELIVERIES.clear()    # sidecar memory loss
                    with contextlib.redirect_stdout(io.StringIO()):
                        r = srv.handle({"command": "agent:reopen-gate", "id": "2",
                                        "args": [json.dumps({
                                            "run_id": "re-2", "gate": "G0",
                                            "reason": "purpose changed",
                                            "role": "PE"})]})
                    self.assertTrue(r["ok"], r)
                    self.assertEqual(r["data"]["status"], "reopened")
                    # one-shot: not re-registered as active
                    self.assertNotIn("re-2", srv._ACTIVE_DELIVERIES)
                    sf = (Path(d) / ".signalos" / "agent-runs" / "re-2"
                          / "delivery.json")
                    data = json.loads(sf.read_text(encoding="utf-8"))
                    self.assertEqual(data["status"], "reopened")
                    self.assertEqual(data["signed"], [])
                    self.assertEqual(data["reopens"], {"G0": 1})
                    # then agent:resume picks the walk back up at G0
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        r2 = srv.handle({"command": "agent:resume", "id": "3",
                                         "args": [json.dumps({
                                             "run_id": "re-2",
                                             "provider": "openai",
                                             "model": "gpt-test"})]})
                    self.assertTrue(r2["ok"], r2)
                    self.assertEqual(r2["data"]["gate"], "G0")
                    self.assertIn("re-2", srv._ACTIVE_DELIVERIES)
                finally:
                    os.chdir(cwd)
        finally:
            self._clear_seams(srv)

    def test_reopen_unknown_run_and_missing_reason_error(self):
        import io, contextlib
        import signalos_ipc_server as srv
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    r = srv.handle({"command": "agent:reopen-gate", "id": "1",
                                    "args": [json.dumps({
                                        "run_id": "ghost", "gate": "G1",
                                        "reason": "x"})]})
                    r2 = srv.handle({"command": "agent:reopen-gate", "id": "2",
                                     "args": [json.dumps({
                                         "run_id": "ghost", "gate": "G1"})]})
                self.assertFalse(r["ok"])
                self.assertIn("no active or persisted delivery", r["error"])
                self.assertFalse(r2["ok"])
                self.assertIn("reason", r2["error"])
            finally:
                os.chdir(cwd)


# ---------------------------------------------------------------------------
# Part 3 - scope drift vs later signed gates (#5)
# ---------------------------------------------------------------------------

_SOUL = (
    "Customer onboarding helper for the team. Captures customer feedback\n"
    "and routes it into our planning loop for the product lead.\n"
    "Stakeholders are the engineering team plus the product lead.\n"
    "Success means we ingest ten onboarding tickets per day.\n"
)


def _mk_workspace(*, soul=True, g2=False, g3=False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="signalos-gate-reopen-"))
    (root / ".signalos").mkdir()
    if soul:
        soul_dir = root / "core" / "governance" / "Governance"
        soul_dir.mkdir(parents=True, exist_ok=True)
        (soul_dir / "SOUL-DOCUMENT.md").write_text(_SOUL, encoding="utf-8")
    if g2:
        p = root / "core" / "strategy" / "EXPECTATION_MAP.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Milestone plan: ship the onboarding intake first, "
                     "then the routing loop, monolith architecture.\n",
                     encoding="utf-8")
    if g3:
        p = root / "core" / "strategy" / "DESIGN_NOTE.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Design note: light theme, single-page intake form, "
                     "two screens total.\n", encoding="utf-8")
    return root


class TestLaterGateDrift(unittest.TestCase):
    def test_conflict_with_signed_g2_recommends_reopen(self):
        root = _mk_workspace(g2=True)
        res = detect_scope_drift(
            root, "Scrap the plan - take a different approach to the "
                  "customer onboarding milestones")
        self.assertTrue(res["drifted"])
        self.assertEqual(res["conflicting_gate"], "G2")
        self.assertEqual(res["recommended_action"], "reopen-gate")
        self.assertGreaterEqual(res["confidence"], 0.8)
        self.assertTrue(any(s == "later-gate-conflict:G2"
                            for s in res["signals"]))
        self.assertTrue(res.get("conflicting_summary"))

    def test_conflict_with_signed_g3_recommends_reopen(self):
        root = _mk_workspace(g2=True, g3=True)
        res = detect_scope_drift(
            root, "Change the design to a dark dashboard layout instead of "
                  "the current customer onboarding screens")
        self.assertTrue(res["drifted"])
        self.assertEqual(res["conflicting_gate"], "G3")
        self.assertEqual(res["recommended_action"], "reopen-gate")

    def test_no_conflict_when_later_gate_unsigned(self):
        root = _mk_workspace(g2=False, g3=False)
        res = detect_scope_drift(
            root, "Scrap the plan - take a different approach to the "
                  "customer onboarding milestones")
        self.assertNotIn("conflicting_gate", res)
        self.assertNotEqual(res["recommended_action"], "reopen-gate")

    def test_plain_refinement_never_flagged(self):
        root = _mk_workspace(g2=True, g3=True)
        res = detect_scope_drift(
            root, "Please also add an onboarding checklist for new "
                  "customer accounts")
        self.assertFalse(res["drifted"])
        self.assertNotIn("conflicting_gate", res)

    def test_g0_drift_detection_unchanged(self):
        """The pre-#5 G0 heuristics still fire the same way."""
        root = _mk_workspace(g2=True)
        res = detect_scope_drift(
            root, "Financial dashboard tracking quarterly investor returns")
        self.assertTrue(res["drifted"])
        self.assertNotIn("conflicting_gate", res)
        self.assertIn(res["recommended_action"], {"amend", "new-project"})


class TestScopeDriftOptionE(unittest.TestCase):
    _CONFLICT_REQUEST = ("Scrap the plan - take a different approach to the "
                         "customer onboarding milestones")

    def _drifted_engine(self) -> WaveEngine:
        root = _mk_workspace(g2=True)
        eng = WaveEngine(root, rehydrate=False)
        result = eng.begin(self._CONFLICT_REQUEST)
        assert result["action"] == "scope-drift-prompt", result
        assert eng.state is WaveState.SCOPE_DRIFT, eng.state
        return eng

    def test_option_e_returns_reopen_action(self):
        eng = self._drifted_engine()
        res = eng.resolve_scope_drift("e")
        self.assertEqual(res["action"], "reopen-gate")
        self.assertEqual(res["gate"], "G2")
        self.assertEqual(res["reason"], self._CONFLICT_REQUEST)
        self.assertEqual(eng.state, WaveState.INSPECT)

    def test_reopen_alias_accepted(self):
        eng = self._drifted_engine()
        self.assertEqual(eng.resolve_scope_drift("reopen")["action"],
                         "reopen-gate")

    def test_existing_four_options_unchanged(self):
        for choice, action in (("a", "fire-agent-G0"),
                               ("b", "new-project-same-workspace"),
                               ("c", "new-project-new-workspace"),
                               ("d", "treat-as-refinement")):
            eng = self._drifted_engine()
            self.assertEqual(eng.resolve_scope_drift(choice)["action"], action)

    def test_unknown_choice_error_mentions_e(self):
        eng = self._drifted_engine()
        with self.assertRaisesRegex(ValueError, "a/b/c/d/e"):
            eng.resolve_scope_drift("zzz")

    def test_ipc_scope_drift_resolve_accepts_choice_e(self):
        import io, contextlib
        import signalos_ipc_server as srv
        root = _mk_workspace(g2=True)
        cwd = os.getcwd()
        os.chdir(root)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r = srv.handle({"command": "wave:scope-drift-resolve",
                                "id": "1",
                                "args": [self._CONFLICT_REQUEST, "e"]})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["data"]["action"], "reopen-gate")
            self.assertEqual(r["data"]["gate"], "G2")
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
