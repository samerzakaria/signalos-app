"""Full-delivery E2E (T39-T44).

Deterministic (Required CI):
  T40 - "medical records HIPAA" -> compliance flags HIPAA + GDPR/PII.
  T43 - closeout is honest (partial, not "ready") when proofs are missing.
  T44 - sidecar crash -> delivery resumes from the persisted checkpoint (INV-5).

Live smoke (optional, skipped without a provider key):
  T39/T41/T42 - a real provider building through the gate walk.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signalos_lib.harness import AgentResponse, TokenUsage
from signalos_lib.product.enforcement_state import StaticEnforcementProvider
from signalos_lib.product import gate_orchestrator as go
from signalos_lib.product import security_gate, closeout


class _EndAdapter:
    supports_tool_calls = True
    def chat(self, messages, model="test", tools=None, stream=False):
        return AgentResponse(content="done", tool_calls=None,
                             stop_reason="end_turn", usage=TokenUsage())


class TestComplianceFlagging(unittest.TestCase):
    def test_hipaa_and_pii_flagged(self):  # T40
        intent = {
            "security_constraints": ["HIPAA"],
            "entities": ["patient", "email", "ssn", "diagnosis"],
        }
        reqs = security_gate.get_compliance_requirements(intent)
        self.assertIn("HIPAA", reqs)
        pii = security_gate.detect_pii_entities(intent["entities"])
        self.assertTrue(pii, "expected PII entities to be detected")
        self.assertIn("GDPR", reqs)  # PII implies GDPR


class TestHonestCloseout(unittest.TestCase):
    def test_partial_when_proofs_missing(self):  # T43
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".signalos").mkdir(parents=True, exist_ok=True)
            co = closeout.build_closeout(root, "TestProduct", "generic", None)
            self.assertNotEqual(co.get("closure_level"), "ready",
                                f"empty repo must not close as ready: {co.get('closure_level')}")
            honesty = closeout.check_closeout_honesty(co)
            self.assertTrue(honesty["honest"], f"closeout overstated readiness: {honesty}")


class TestDeliveryResume(unittest.TestCase):
    def test_resume_from_checkpoint(self):  # T44
        signed = []
        def fake_sign(root, gate, signer, role, verdict, conditions):
            signed.append(gate); return [f"{gate}.md"]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Run a delivery part-way: sign G0, G1 -> now paused at G2.
            orch = go.GateOrchestrator(root, _EndAdapter(), lambda e: None,
                                       enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                                       sign_fn=fake_sign, prompt="build a tracker", run_id="del-x")
            orch.start()
            orch.apply_verdict("approve")   # G0 -> G1
            orch.apply_verdict("approve")   # G1 -> G2
            self.assertEqual(orch.state.current_gate, "G2")
            # "Crash": drop the object, resume from disk.
            del orch
            events = []
            resumed = go.resume_delivery(root, "del-x", _EndAdapter(), events.append,
                                         enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                                         sign_fn=fake_sign)
            self.assertEqual(resumed.state.current_gate, "G2")
            self.assertEqual(resumed.state.signed, ["G0", "G1"])
            # ...and it can continue from there.
            res = resumed.apply_verdict("approve")  # sign G2 -> G3
            self.assertEqual(res["status"], "advanced")
            self.assertEqual(res["gate"], "G3")


_LIVE = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")


@unittest.skipUnless(_LIVE, "live provider key required (T39/T41/T42 smoke)")
class TestLiveDeliverySmoke(unittest.TestCase):
    def test_build_walks_gates_with_real_provider(self):  # T39 (smoke)
        from signalos_lib.product.provider_adapter import ProviderAdapter
        with tempfile.TemporaryDirectory() as d:
            events = []
            orch = go.GateOrchestrator(
                Path(d), ProviderAdapter(model=os.getenv("SIGNALOS_MODEL", "claude-sonnet-4-5")),
                events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T2"),
                prompt="Build a task management app for my team",
            )
            res = orch.start()
            self.assertEqual(res["gate"], "G0")
            self.assertTrue(any(e.get("type") == "gate" for e in events))


if __name__ == "__main__":
    unittest.main()
