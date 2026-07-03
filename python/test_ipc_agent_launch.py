# test_ipc_agent_launch.py
# 3.4 (C-bridge), IPC layer: agent:launch -> start_launch_build -> a real
# GateOrchestrator walk in an isolated child repo_root, driven through
# srv.handle() exactly the way the desktop app would call it. Mirrors
# test_agent_ipc.py's _AgentIpcBase seam pattern for agent:deliver.

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import signalos_ipc_server as srv  # noqa: E402

from signalos_lib.harness import AgentTestProvider, TokenUsage, AgentResponse  # noqa: E402
from signalos_lib.product.closeout import write_closeout  # noqa: E402
from signalos_lib.product.enforcement_state import StaticEnforcementProvider  # noqa: E402
from signalos_lib.product.launch import list_launches, load_launch_link  # noqa: E402
from signalos_lib.product.provider_adapter import (  # noqa: E402
    ProviderAdapter,
    ProviderCapabilities,
)


def _end_resp(text: str = "(gate work done)") -> AgentResponse:
    return AgentResponse(content=text, tool_calls=None, stop_reason="end_turn", usage=TokenUsage(1, 1))


def _adapter_factory():
    def factory(model: str, provider: str | None = None) -> ProviderAdapter:
        test_provider = AgentTestProvider(script=[_end_resp() for _ in range(6)])
        caps = ProviderCapabilities(model=model, supports_tool_calls=True,
                                     supports_streaming=True, context_length=200_000)
        return ProviderAdapter(model=model, provider=test_provider, capabilities=caps)
    return factory


def _parse_lines(captured: str) -> list[dict]:
    out = []
    for line in captured.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class TestAgentLaunchIpc(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        srv._AGENT_ENFORCEMENT_FACTORY = lambda: StaticEnforcementProvider(trust_tier="T3")
        srv._AGENT_ADAPTER_FACTORY = _adapter_factory()
        srv._AGENT_CANCEL_FLAGS.clear()
        srv._ACTIVE_DELIVERIES.clear()

        signalos_dir = Path(self._tmp.name) / ".signalos"
        write_closeout(
            {"product_name": "Acme Tracker", "profile": "react-vite", "closure_level": "complete"},
            signalos_dir,
        )

    def tearDown(self) -> None:
        srv._AGENT_ADAPTER_FACTORY = None
        srv._AGENT_ENFORCEMENT_FACTORY = None
        srv._AGENT_CANCEL_FLAGS.clear()
        srv._ACTIVE_DELIVERIES.clear()
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def _run(self, req: dict) -> tuple[dict, list[dict]]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            resp = srv.handle(req)
        return resp, _parse_lines(buf.getvalue())

    def test_agent_launch_walks_a_real_gate_in_an_isolated_child_root(self) -> None:
        resp, events = self._run({
            "command": "agent:launch",
            "id": "req-launch-1",
            "args": [json.dumps({"provider": "openai", "model": "gpt-test"})],
        })
        self.assertTrue(resp["ok"], msg=resp)

        result = resp["data"]
        child_root = Path(result["child_repo_root"])
        # child_repo_root is derived from os.getcwd(), which resolves symlinks
        # (on macOS /var -> /private/var), so compare RESOLVED paths -- a raw
        # string startswith against the unresolved tmp dir is a false negative
        # there. The behavioral claim is "the child lives under the parent".
        tmp_resolved = Path(self._tmp.name).resolve()
        self.assertTrue(child_root.resolve().is_relative_to(tmp_resolved))
        self.assertNotEqual(child_root.resolve(), tmp_resolved)

        agent_events = [e for e in events if e.get("kind") == "agent-event"]
        self.assertTrue(agent_events, events)
        gate_events = [e for e in agent_events if e.get("type") == "gate"]
        self.assertTrue(gate_events, agent_events)
        self.assertEqual(gate_events[0]["gate"], "G0")

        link = load_launch_link(child_root)
        self.assertIsNotNone(link)
        self.assertEqual(link["parent_product_name"], "Acme Tracker")
        self.assertEqual(len(list_launches(Path(self._tmp.name))), 1)

    def test_agent_launch_carries_founder_identity_into_the_child_signer(self) -> None:
        (Path(self._tmp.name) / ".signalos" / "identity.json").write_text(
            json.dumps({"name": "Samer Zakaria", "role": "PO"}), encoding="utf-8",
        )
        resp, _ = self._run({
            "command": "agent:launch",
            "id": "req-launch-identity",
            "args": [json.dumps({"provider": "openai", "model": "gpt-test"})],
        })
        self.assertTrue(resp["ok"], msg=resp)
        run_id = resp["data"]["run_id"]
        self.assertEqual(srv._ACTIVE_DELIVERIES[run_id].signer, "Samer Zakaria (PO)")

    def test_agent_launch_requires_model_before_touching_the_repo(self) -> None:
        resp, _ = self._run({
            "command": "agent:launch",
            "id": "req-launch-2",
            "args": [json.dumps({"provider": "openai"})],
        })
        self.assertFalse(resp["ok"])
        self.assertIn("model", resp["error"])

    def test_agent_launch_refuses_without_a_parent_closeout(self) -> None:
        with tempfile.TemporaryDirectory() as bare:
            os.chdir(bare)
            try:
                resp, _ = self._run({
                    "command": "agent:launch",
                    "id": "req-launch-3",
                    "args": [json.dumps({"provider": "openai", "model": "gpt-test"})],
                })
            finally:
                os.chdir(self._tmp.name)
            self.assertFalse(resp["ok"])
            self.assertIn("closeout", resp["error"])


if __name__ == "__main__":
    unittest.main()
