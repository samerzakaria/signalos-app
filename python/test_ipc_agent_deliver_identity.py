# test_ipc_agent_deliver_identity.py
# 3.6 (C-bridge), IPC layer: agent:deliver now threads the founder's real
# identity (.signalos/identity.json, set once via the onboarding wizard)
# into the GateOrchestrator's signer -- previously every real gate signature
# was recorded under the generic literal "foundry-agent" because nothing on
# the Python side ever read identity.json at all.

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agent_ipc import _AgentIpcBase, _adapter_factory, _agent_args, _end_resp

import signalos_ipc_server as srv  # noqa: E402


class TestAgentDeliverIdentity(_AgentIpcBase):
    def test_real_identity_reaches_the_orchestrator_signer(self) -> None:
        identity_path = Path(self._tmp.name) / ".signalos"
        identity_path.mkdir(parents=True, exist_ok=True)
        (identity_path / "identity.json").write_text(
            json.dumps({"name": "Samer Zakaria", "role": "PO"}), encoding="utf-8",
        )

        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("(gate work done)")])
        resp, _ = self._run({
            "command": "agent:deliver",
            "id": "req-deliver-identity",
            "args": [_agent_args(prompt="build a task tracker", run_id="run-identity")],
        })
        self.assertTrue(resp["ok"], msg=resp)
        orch = srv._ACTIVE_DELIVERIES["run-identity"]
        self.assertEqual(orch.signer, "Samer Zakaria (PO)")

    def test_falls_back_to_generic_signer_when_identity_unset(self) -> None:
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory([_end_resp("(gate work done)")])
        resp, _ = self._run({
            "command": "agent:deliver",
            "id": "req-deliver-no-identity",
            "args": [_agent_args(prompt="build a task tracker", run_id="run-no-identity")],
        })
        self.assertTrue(resp["ok"], msg=resp)
        orch = srv._ACTIVE_DELIVERIES["run-no-identity"]
        self.assertEqual(orch.signer, "foundry-agent")


if __name__ == "__main__":
    import unittest
    unittest.main()
