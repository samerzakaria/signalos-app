"""Full-delivery E2E (T39-T44).

Required CI:
  T40 - "medical records HIPAA" -> compliance flags HIPAA + GDPR/PII.
  T43 - closeout is honest (partial, not "ready") when proofs are missing.
  T44 - sidecar crash -> delivery resumes from the persisted checkpoint (INV-5).

Provider smoke:
  T39/T41/T42 - use a real provider when a key is configured; otherwise use
  the deterministic adapter so CI never records a skipped gate-walk proof.
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
            # "Crash": the old process no longer owns the delivery lock, then
            # reconstruction resumes only from the durable checkpoint.
            orch._release_delivery_lock()
            del orch
            events = []
            resumed = go.resume_delivery(root, "del-x", _EndAdapter(), events.append,
                                         enforcement_provider=StaticEnforcementProvider(trust_tier="T3"),
                                         sign_fn=fake_sign)
            self.assertEqual(resumed.state.current_gate, "G2")
            self.assertEqual(resumed.state.signed, ["G0", "G1"])
            self.assertEqual(resumed.state.status, "blocked")
            # G2's no-tool outcome was never reviewable. Restarting must not
            # manufacture an awaiting-verdict checkpoint or let a custom
            # signer approve it; it remains at G2 until the work is rerun.
            res = resumed.apply_verdict("approve")
            self.assertEqual(res["status"], "not-reviewable")
            self.assertEqual(res["gate"], "G2")
            self.assertEqual(signed, ["G0", "G1"])
            self.assertTrue(any(e.get("type") == "gate_blocked"
                                and e.get("gate") == "G2" for e in events))


# Live provider path is OPT-IN (SIGNALOS_LIVE_E2E=1) so the offline suite is
# deterministic and immune to an ambient/polluted provider key: a real key
# leaking into the shell used to flip this to the live ProviderAdapter and then
# error the run (e.g. an out-of-credit key). Real live e2e runs use the
# standalone delivery scripts, not this smoke test.
_LIVE = bool(os.getenv("SIGNALOS_LIVE_E2E")) and bool(
    os.getenv("ANTHROPIC_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or os.getenv("GEMINI_API_KEY")
)


class TestDeliverySmoke(unittest.TestCase):
    def test_build_walks_gates_with_provider_or_deterministic_adapter(self):  # T39
        if _LIVE:
            from signalos_lib.product.provider_adapter import ProviderAdapter
            adapter = ProviderAdapter(model=os.getenv("SIGNALOS_MODEL", "claude-sonnet-4-5"))
        else:
            adapter = _EndAdapter()

        with tempfile.TemporaryDirectory() as d:
            events = []
            orch = go.GateOrchestrator(
                Path(d), adapter,
                events.append,
                enforcement_provider=StaticEnforcementProvider(trust_tier="T2"),
                prompt="Build a task management app for my team",
            )
            res = orch.start()
            self.assertEqual(res["gate"], "G0")
            self.assertTrue(any(e.get("type") == "gate" for e in events))
            # A gate firing on a failed provider call proves plumbing, not a
            # real response. When running live, the loop must NOT have errored
            # (INV-4 surfaces provider failures as an error event).
            errs = [e for e in events if e.get("type") == "error"]
            self.assertFalse(errs, f"provider call errored: {errs[:1]}")


if __name__ == "__main__":
    unittest.main()
